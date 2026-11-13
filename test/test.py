# SPDX-FileCopyrightText: © 2026 MAC Attack
# SPDX-License-Identifier: Apache-2.0
#
# Tiny-TPU v2 tests. The test plays the host (the RP2040): it keeps the model, loads each layer's
# inputs into the chip once, streams one weight byte per MAC, reads the 22-bit sums, and does the
# rest of each layer itself. Two real models, both must match their reference exactly:
#   1. the professor's INT4 8-4-2 (golden_vectors.json): every READ = expected_acc
#   2. our modelV2 INT8 13-32-16-4 (modelV2/quant.json): the host does modelV2's integer
#      requantization + ReLU between layers; the last layer's sums = vectors_C.hex for all 64 rows
# Plus edge cases: INT8 products, the full 32-entry buffer, the 22-bit overflow flag, both modes.

import json
import random
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge, Timer

NOP, CLEAR, LOAD_X, MAC, READ = 0, 1, 2, 3, 4
HERE = Path(__file__).parent
ACC = 22


class Host:
    def __init__(self, dut, mode):
        self.dut = dut
        self.mode = mode                  # 0 = STROBE/ACK handshake, 1 = streaming (one command per clock)
        self.bsel = 0

    def _uio(self, cmd, strobe):
        return (cmd & 7) | (strobe << 3) | (self.mode << 4) | (self.bsel << 5)

    async def run(self, ops):
        """ops: list of (cmd, data byte). Runs them in order."""
        dut = self.dut
        if self.mode == 1:
            for cmd, data in ops:                       # drive between clock edges, one command per clock
                await FallingEdge(dut.clk)
                dut.ui_in.value = data & 0xFF
                dut.uio_in.value = self._uio(cmd, 1)
            await FallingEdge(dut.clk)
            dut.uio_in.value = self._uio(NOP, 0)
            return
        for cmd, data in ops:
            dut.ui_in.value = data & 0xFF
            dut.uio_in.value = self._uio(cmd, 0)
            await ClockCycles(dut.clk, 1)
            dut.uio_in.value = self._uio(cmd, 1)
            await self._ack(1)
            dut.uio_in.value = self._uio(cmd, 0)
            await self._ack(0)

    async def _ack(self, level):
        for _ in range(50):
            await RisingEdge(self.dut.clk)
            await Timer(1, unit="ns")
            if int(self.dut.uio_out.value) >> 7 == level:
                return
        raise AssertionError(f"ACK never went {level}")

    async def _byte(self, sel):
        self.bsel = sel
        self.dut.uio_in.value = self._uio(NOP, 0)
        await Timer(1, unit="ns")
        v = int(self.dut.uo_out.value)
        self.bsel = 0
        self.dut.uio_in.value = self._uio(NOP, 0)
        return v

    async def read_sum(self):
        """22-bit signed sum from the last READ (3 bytes)."""
        b0, b1, b2 = await self._byte(0), await self._byte(1), await self._byte(2)
        v = b0 | (b1 << 8) | ((b2 & 0x3F) << 16)
        return v - (1 << ACC) if v & (1 << (ACC - 1)) else v

    async def status(self):
        s = await self._byte(3)
        return s & 1, (s >> 1) & 0x3F                   # ovf, buffer position

    # ---- one layer, the host's way: inputs once, then one weight byte per MAC per neuron
    async def layer(self, x, W):
        """x: n inputs (signed); W[j]: the n weights of neuron j. Returns the n_out raw sums."""
        await self.run([(CLEAR, 0)] + [(LOAD_X, v) for v in x])
        sums = []
        for wj in W:
            await self.run([(CLEAR, 0)] + [(MAC, w) for w in wj] + [(READ, 0)])
            sums.append(await self.read_sum())
            ovf, _ = await self.status()
            assert ovf == 0, "unexpected overflow"
        return sums


async def reset(dut):
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())   # 50 MHz
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)


def requant(acc, bias, m0, sh, relu):
    """modelV2's integer step between layers (rtl/postproc.sv, mac_test.c requant): exact integers."""
    y = ((acc + bias) * m0 + (1 << (sh - 1))) >> sh
    return max(0 if relu else -128, min(127, y))


@cocotb.test()
async def test_int4_professor_model(dut):
    """The professor's INT4 8-4-2: all 12 golden inferences, handshake mode."""
    await reset(dut)
    host = Host(dut, mode=0)
    vectors = json.loads((HERE / "golden_vectors.json").read_text())
    reads = 0
    for i, vec in enumerate(vectors):
        # regroup the golden CLEAR/MAC/READ stream into layers: same inputs a for every neuron of a layer
        neurons, cur = [], None
        for op in vec["commands"]:
            if op["op"] == "CLEAR":
                cur = {"layer": op["layer"], "a": [], "w": []}
            elif op["op"] == "MAC":
                cur["a"].append(op["a"])
                cur["w"].append(op["w"])
            else:
                cur["expected"] = op["expected_acc"]
                neurons.append(cur)
        for layer in sorted({n["layer"] for n in neurons}):
            ns = [n for n in neurons if n["layer"] == layer]
            assert all(n["a"] == ns[0]["a"] for n in ns), "a layer's neurons share their inputs"
            sums = await host.layer(ns[0]["a"], [n["w"] for n in ns])
            for n, s in zip(ns, sums):
                assert s == n["expected"], f"vector {i} layer {layer}: {s} != {n['expected']}"
                reads += 1
    dut._log.info(f"professor's INT4 model: {len(vectors)} inferences, {reads} sums, all exact")


def load_modelv2():
    q = json.loads((HERE / "modelV2" / "quant.json").read_text())
    layers = q["layers"]
    if isinstance(layers, str):
        import ast
        layers = ast.literal_eval(layers)
    sizes = q["sizes"]
    xs = [int(l, 16) for l in (HERE / "modelV2" / "vectors_X.hex").read_text().split()]
    xs = [v - 256 if v > 127 else v for v in xs]
    X = [xs[r * sizes[0]:(r + 1) * sizes[0]] for r in range(len(xs) // sizes[0])]
    cs = [int(l, 16) for l in (HERE / "modelV2" / "vectors_C.hex").read_text().split()]
    cs = [v - (1 << 22) if v & (1 << 21) else v for v in cs]
    C = [cs[r * sizes[-1]:(r + 1) * sizes[-1]] for r in range(len(cs) // sizes[-1])]
    return layers, sizes, X, C


async def run_modelv2(host, layers, x):
    a = x
    for li, L in enumerate(layers):
        W = L["W"]                                           # K x N
        cols = [[W[k][j] for k in range(len(W))] for j in range(len(W[0]))]
        sums = await host.layer(a, cols)
        if li == len(layers) - 1:
            return sums                                      # last layer: raw sums, like the FPGA chip
        a = [requant(s, L["bias"][j], L["m0"][j], L["shift"][j], L["relu"]) for j, s in enumerate(sums)]


@cocotb.test()
async def test_int8_modelv2_streaming(dut):
    """Our modelV2 INT8 13-32-16-4, all 64 reference rows, streaming mode."""
    await reset(dut)
    host = Host(dut, mode=1)
    layers, sizes, X, C = load_modelv2()
    assert max(sizes[:-1]) <= 32, "every layer input fits the 32-byte buffer"
    for r, (x, c) in enumerate(zip(X, C)):
        got = await run_modelv2(host, layers, x)
        assert got == c, f"row {r}: {got} != {c}"
    macs = sum(len(L["W"]) * len(L["W"][0]) for L in layers)
    dut._log.info(f"modelV2 {'-'.join(map(str, sizes))}: {len(X)} rows, {macs} MACs per row, every output sum exact")


@cocotb.test()
async def test_int8_modelv2_handshake(dut):
    """The same model in handshake mode (first 3 rows): both modes give the same sums."""
    await reset(dut)
    host = Host(dut, mode=0)
    layers, _, X, C = load_modelv2()
    for r in range(3):
        assert await run_modelv2(host, layers, X[r]) == C[r], f"row {r}"
    dut._log.info("modelV2 in handshake mode: rows 0-2 exact")


@cocotb.test()
async def test_edge_cases(dut):
    await reset(dut)
    host = Host(dut, mode=1)

    # INT8 products: every x against the extreme and small weights, plus random pairs
    pairs = [(x, w) for x in range(-128, 128) for w in (-128, -127, -1, 0, 1, 127)]
    rng = random.Random(5)
    pairs += [(rng.randint(-128, 127), rng.randint(-128, 127)) for _ in range(500)]
    for x, w in pairs:
        await host.run([(CLEAR, 0), (LOAD_X, x), (CLEAR, 0), (MAC, w), (READ, 0)])
        assert await host.read_sum() == x * w, f"{x} * {w}"
    dut._log.info(f"{len(pairs)} INT8 products exact")

    # full 32-entry buffer: distinct inputs, weights 1..32, then the position wraps to 0
    x = [rng.randint(-128, 127) for _ in range(32)]
    w = list(range(1, 33))
    assert await host.layer(x, [w]) == [sum(a * b for a, b in zip(x, w))]
    assert await host.status() == (0, 0), "position wrapped to 0 after 32 MACs"
    dut._log.info("32-entry buffer: exact")

    # 22-bit overflow: 127 x (-128 * -128) = 2,080,768 fits; the 128th product passes 2^21 - 1
    await host.run([(CLEAR, 0)] + [(LOAD_X, -128)] * 32 + [(CLEAR, 0)] + [(MAC, -128)] * 127 + [(READ, 0)])
    assert await host.read_sum() == 127 * 16384
    assert (await host.status())[0] == 0
    await host.run([(MAC, -128), (READ, 0)])
    assert (await host.status())[0] == 1, "overflow flagged"
    await host.run([(CLEAR, 0), (READ, 0)])
    assert await host.read_sum() == 0 and (await host.status())[0] == 0, "CLEAR clears the flag"
    dut._log.info("22-bit overflow flagged and cleared")
