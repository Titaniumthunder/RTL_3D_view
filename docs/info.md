<!---

This file is used to generate your project datasheet. Please fill in the information below and delete any unused
sections.

You can also include images in this folder and reference them in the markdown. Each image must be less than
512 kb in size, and the combined size of all images must be less than 1 MB.
-->

## How it works

Tiny-TPU v2 is one processing element of a neural-network accelerator by team MAC Attack (Alex Salamati,
James Manikyan, Marco Flores), for a motor-vibration fault detector.

It multiplies signed 8-bit numbers (INT8) and adds the products into an exact 22-bit sum. A 32-byte **input
buffer** holds one layer's inputs, so the host sends a layer's inputs once and then only **one weight byte per
multiply-add**. The host (the RP2040 on the demo board) keeps the model, runs it layer by layer, and does each
layer's bias, rescaling and ReLU between layers. Any model whose layers have at most 32 inputs can run without
changing the chip; INT4 models work too.

Commands: `CLEAR` (sum = 0, back to buffer entry 0), `LOAD_X` (store the next input), `MAC` (sum += next stored
input x the weight on DATA), `READ` (copy the sum to the output register), `NOP`. A sum that leaves the 22-bit
range sets the OVF status flag until the next `CLEAR`.

## How to test

Pins: DATA on `ui_in[7:0]`; CMD on `uio_in[2:0]` (0 NOP, 1 CLEAR, 2 LOAD_X, 3 MAC, 4 READ); STROBE/VALID on
`uio_in[3]`; MODE on `uio_in[4]`; BSEL on `uio_in[6:5]`; ACK on `uio_out[7]`. `uo_out` shows sum bits 7:0
(BSEL 0), 15:8 (BSEL 1), 21:16 sign-extended (BSEL 2), or the status byte {0, buffer position, OVF} (BSEL 3).

MODE 0 (handshake, any host speed): set DATA and CMD, raise STROBE, wait for ACK = 1, lower STROBE, wait for
ACK = 0. MODE 1 (streaming): every rising clock edge with VALID = 1 runs CMD on DATA, one command per clock;
DATA, CMD and VALID must be synchronous to the clock.

One neuron with inputs 3, -2, 1 and weights 2, 4, -1: `CLEAR`, `LOAD_X 3`, `LOAD_X -2`, `LOAD_X 1`, `CLEAR`,
`MAC 2`, `MAC 4`, `MAC -1`, `READ`, then the three sum bytes give 6 - 8 - 1 = -3.

## External hardware

None beyond the Tiny Tapeout demo board: its RP2040 acts as the host.
