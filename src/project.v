/*
 * Copyright (c) 2026 MAC Attack (Alex Salamati, James Manikyan, Marco Flores)
 * SPDX-License-Identifier: Apache-2.0
 *
 * MAC Attack mini: our INT8 weight-stationary systolic array, sized for Tiny Tapeout.
 *
 * This file only adapts the MAC Attack chip to Tiny Tapeout's pins. The chip itself is
 * mac_attack_chip.v, our team's RTL (sv2v output of the SystemVerilog in rtl/, unchanged); here it is
 * built with:
 *   P    = 2      2x2 array = 4 processing elements (4 multiply-adds per clock)
 *   MAXD = 4      matrices up to 4x4 are stored on chip; bigger ones run in passes (folding)
 *   ACC  = 18     exact sums: 2*8 bits + log2(4) = 18, so no result can overflow
 *   CLKS_PER_BIT = 434   UART at 115,200 baud from the 50 MHz Tiny Tapeout clock
 *   PIPE = 1, MEM_REG = 1, HREAD_REG = 0   the original single-register settings: the extra
 *                  registers of the 250 MHz FPGA build are not needed at 50 MHz
 *
 * Pins: ui_in[0] = UART RX (into the chip), uo_out[0] = UART TX (out of the chip),
 *       uo_out[1] = busy (high while the array computes). All other pins are unused.
 */

`default_nettype none

module tt_um_titaniumthunder_mac_attack (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // always 1 when the design is powered, so you can ignore it
    input  wire       clk,      // clock
    input  wire       rst_n     // reset_n - low to reset
);

  wire uart_tx;
  wire busy;

  chip_top #(
      .P           (2),
      .BITS        (8),
      .ACC         (18),
      .SAT         (0),
      .MAXD        (4),
      .CLKS_PER_BIT(434),
      .PIPE        (1),
      .MEM_REG     (1),
      .HREAD_REG   (0)
  ) u_chip (
      .clk      (clk),
      .rst      (~rst_n),
      .uart_rx_i(ui_in[0]),
      .uart_tx_o(uart_tx),
      .busy_led (busy)
  );

  assign uo_out  = {6'b000000, busy, uart_tx};
  assign uio_out = 8'b00000000;
  assign uio_oe  = 8'b00000000;

  // List all unused inputs to prevent warnings
  wire _unused = &{ena, ui_in[7:1], uio_in, 1'b0};

endmodule

`default_nettype wire
