<!---

This file is used to generate your project datasheet. Please fill in the information below and delete any unused
sections.

You can also include images in this folder and reference them in the markdown. Each image must be less than
512 kb in size, and the combined size of all images must be less than 1 MB.
-->

## How it works

MAC Attack mini is a small matrix-multiply accelerator: a scaled-down version of our capstone "mini-TPU"
(team MAC Attack: Alex Salamati, James Manikyan, Marco Flores). It computes C = A x B on signed 8-bit (INT8)
numbers, exactly.

- **2x2 weight-stationary systolic array** (4 processing elements). Each element multiplies two INT8 numbers
  and adds the product to a running sum, every clock. A 2x2 block of B (the weights) is loaded into the array
  and stays there; the rows of A flow across it and the partial sums flow down.
- **Folding:** matrices up to 4x4 are stored on chip. A bigger multiply than 2x2 is done in passes, one 2x2
  block of B per pass, and the passes add up in the C buffer. A 4x4 x 4x4 multiply takes 4 passes.
  Clocks per job: passes x (M + 3P - 2), with passes = ceil(K/P) x ceil(N/P) and P = 2.
- **Exact 18-bit sums:** the largest product is 128 x 128 = 16384, and 4 of them need 18 signed bits, so a
  result can never overflow.
- **UART interface** at 115,200 baud (8N1) from the 50 MHz clock.

The same RTL runs on our FPGA prototype as an 8x8 array with matrices up to 64x64; only the size parameters
differ here.

## How to test

Connect a UART (115,200 baud, 8 data bits, no parity, 1 stop bit) to the chip: your TX to `ui_in[0]`, your RX
to `uo_out[0]`. Keep `ui_in[0]` high when idle. Commands (all numbers are bytes; results are 4 bytes,
least significant byte first):

| Send | Reply | Meaning |
|---|---|---|
| `I` | `M` `A` `C` 2 8 18 4 | identify: P, input bits, sum bits, largest matrix |
| `D` m k n | `k` (or `e` if a size is 0 or more than 4) | sizes: A is m x k, B is k x n |
| `A` + m*k bytes | `k` | load A, row by row (two's complement) |
| `B` + k*n bytes | `k` | load B, row by row |
| `G` | `g` + passes + cycles + total | run; the counters are 4 bytes each |
| `C` | m*n results, 4 bytes each | read C, row by row |

Example: `D 4 4 4`, then `A` and 16 bytes, `B` and 16 bytes, `G` (reply: 4 passes, 32 cycles), then `C`
returns the 16 sums. `uo_out[1]` is high while the array is computing.

## External hardware

A USB-UART adapter (3.3 V), or the UART of the Tiny Tapeout demo board's microcontroller.
