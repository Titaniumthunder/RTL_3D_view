# SPDX-FileCopyrightText: © 2026 MAC Attack
# SPDX-License-Identifier: Apache-2.0
#
# Plays the laptop on the other end of the chip's UART (115,200 baud = 434 clocks per bit at
# 50 MHz) and checks every result against the same integer matrix multiply done in Python.
#
# Protocol (rtl/host_if.sv):
#   'I'                 -> 'M' 'A' 'C' P BITS ACC MAXD
#   'D' m k n           -> 'k'                (sizes, each 1..MAXD)
#   'A' + m*k bytes     -> 'k'                (A row by row, INT8)
#   'B' + k*n bytes     -> 'k'                (B row by row, INT8)
#   'G'                 -> 'g' + passes + cycles + total   (4 bytes each, LSB first)
#   'C'                 -> m*n results, 4 bytes each, LSB first

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

CLKS_PER_BIT = 434   # must match project.v
P, BITS, ACC, MAXD = 2, 8, 18, 4


class Uart:
    def __init__(self, dut):
        self.dut = dut
        self.rx = []                       # bytes received from the chip
        self.pos = 0                       # how many of them the test has read

    async def monitor(self):
        """Decodes every byte the chip sends on uo_out[0]."""
        dut = self.dut
        while True:
            await FallingEdge(dut.uart_tx)                     # start bit
            await ClockCycles(dut.clk, CLKS_PER_BIT // 2)
            b = 0
            for i in range(8):
                await ClockCycles(dut.clk, CLKS_PER_BIT)
                b |= (int(dut.uart_tx.value) & 1) << i
            await ClockCycles(dut.clk, CLKS_PER_BIT)           # stop bit
            self.rx.append(b)

    async def send(self, data):
        """Sends bytes to the chip on ui_in[0] (idle high)."""
        dut = self.dut
        for byte in data:
            bits = [0] + [(byte >> i) & 1 for i in range(8)] + [1]   # start, 8 data, stop
            for bit in bits:
                dut.ui_in.value = bit
                await ClockCycles(dut.clk, CLKS_PER_BIT)

    async def recv(self, n):
        """Waits for the next n bytes from the chip (about 10 bit times per byte, plus margin)."""
        for _ in range(20 * (n + 2)):
            if len(self.rx) >= self.pos + n:
                break
            await ClockCycles(self.dut.clk, CLKS_PER_BIT)
        got = self.rx[self.pos:self.pos + n]
        assert len(got) == n, f"expected {n} bytes, got {len(got)}: {got}"
        self.pos += n
        return got


def to_u8(v):
    return v & 0xFF


def le32(bs):
    v = bs[0] | (bs[1] << 8) | (bs[2] << 16) | (bs[3] << 24)
    return v - (1 << 32) if v & 0x80000000 else v


async def run_job(dut, uart, A, B):
    m, k, n = len(A), len(A[0]), len(B[0])
    await uart.send([ord("D"), m, k, n])
    assert await uart.recv(1) == [ord("k")], "set sizes"
    await uart.send([ord("A")] + [to_u8(v) for row in A for v in row])
    assert await uart.recv(1) == [ord("k")], "load A"
    await uart.send([ord("B")] + [to_u8(v) for row in B for v in row])
    assert await uart.recv(1) == [ord("k")], "load B"

    await uart.send([ord("G")])
    g = await uart.recv(13)
    assert g[0] == ord("g"), "run"
    passes, cycles, total = le32(g[1:5]), le32(g[5:9]), le32(g[9:13])
    eq_passes = -(-k // P) * -(-n // P)                 # ceil(K/P) * ceil(N/P)
    eq_cycles = eq_passes * (m + 3 * P - 2)              # Np * (M + 3P - 2)
    dut._log.info(f"{m}x{k}x{n}: passes {passes} (eq {eq_passes}), cycles {cycles} (eq {eq_cycles}), total {total}")
    assert passes == eq_passes and cycles == eq_cycles, "cycle count differs from the equation"

    await uart.send([ord("C")])
    c = await uart.recv(4 * m * n)
    C_hw = [[le32(c[4 * (i * n + j): 4 * (i * n + j) + 4]) for j in range(n)] for i in range(m)]
    C_sw = [[sum(A[i][t] * B[t][j] for t in range(k)) for j in range(n)] for i in range(m)]
    assert C_hw == C_sw, f"C mismatch:\nhardware {C_hw}\nexpected {C_sw}"
    dut._log.info(f"C = {C_hw}  (all {m * n} values exact)")


@cocotb.test()
async def test_project(dut):
    dut._log.info("Start")
    clock = Clock(dut.clk, 20, unit="ns")                # 50 MHz
    cocotb.start_soon(clock.start())

    dut.ena.value = 1
    dut.ui_in.value = 1                                  # UART line idles high
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 10)

    uart = Uart(dut)
    cocotb.start_soon(uart.monitor())

    # 1. Identify: the chip reports its own size
    await uart.send([ord("I")])
    ident = await uart.recv(7)
    assert ident == [ord("M"), ord("A"), ord("C"), P, BITS, ACC, MAXD], f"identify: {ident}"
    dut._log.info(f"Identify: MAC, P={P}, BITS={BITS}, ACC={ACC}, MAXD={MAXD}")

    rng = random.Random(42)
    rnd = lambda r, c: [[rng.randint(-128, 127) for _ in range(c)] for _ in range(r)]

    # 2. Full 4x4 x 4x4 on the 2x2 array: 4 passes (folding), with the extreme values included
    A = rnd(4, 4)
    B = rnd(4, 4)
    A[0] = [-128, -128, -128, -128]
    B[0][0], B[1][0], B[2][0], B[3][0] = -128, -128, -128, -128   # C[0][0] = 4 * 16384, the largest sum
    A[3] = [127, 127, 127, 127]
    await run_job(dut, uart, A, B)

    # 3. Odd sizes, 3x3 x 3x2: partly filled tiles at the edges
    await run_job(dut, uart, rnd(3, 3), rnd(3, 2))
