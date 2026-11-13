/*
 * Copyright (c) 2026 MAC Attack (Alex Salamati, James Manikyan, Marco Flores)
 * SPDX-License-Identifier: Apache-2.0
 *
 * Tiny-TPU v2 for Tiny Tapeout: tiny_tpu_pe8 (INT8 MAC, 22-bit sum, 32-byte input buffer) on the
 * demo board's pins. The host (the board's RP2040) keeps the model and runs it layer by layer:
 *
 *   per layer:   CLEAR, LOAD_X x (number of inputs)          the inputs go in once
 *   per neuron:  CLEAR, MAC x (number of inputs), READ       one weight byte per MAC
 *                then read the 22-bit sum (3 bytes) and do bias / requantize / ReLU on the host
 *
 * Pins
 *   ui_in[7:0]   DATA: an input value (LOAD_X) or a weight (MAC), signed 8-bit
 *   uio_in[2:0]  CMD: 0 NOP, 1 CLEAR, 2 LOAD_X, 3 MAC, 4 READ
 *   uio_in[3]    STROBE (MODE 0) or VALID (MODE 1)
 *   uio_in[4]    MODE: 0 = handshake, works at any host speed; 1 = streaming, one command per clock
 *   uio_in[6:5]  BSEL: which byte uo_out shows: 0 sum[7:0], 1 sum[15:8], 2 sum[21:16] sign-extended,
 *                3 status {0, buffer position[5:0], OVF}
 *   uio_out[7]   ACK (MODE 0)
 *   uo_out[7:0]  the selected byte of the sum latched by the last READ (or the status byte)
 *
 * MODE 0 (handshake): set DATA and CMD, raise STROBE, wait for ACK = 1, lower STROBE, wait for
 *   ACK = 0. STROBE goes through a two-flip-flop synchronizer, so the host can be on any clock.
 * MODE 1 (streaming): every rising clock edge with VALID = 1 runs CMD on DATA, so a whole neuron is
 *   one byte per clock. DATA, CMD and VALID must be synchronous to clk (for example, the RP2040
 *   generating the clock and the data together). Change MODE only while idle.
 */

`default_nettype none

module tt_um_titaniumthunder_tiny_tpu8 (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // always 1 when the design is powered, so you can ignore it
    input  wire       clk,      // clock
    input  wire       rst_n     // reset_n - low to reset
);

  wire rst  = ~rst_n;
  wire mode = uio_in[4];

  // MODE 0: STROBE synchronizer, rising-edge detect, and ACK
  reg s1, s2, s3, ack;
  always @(posedge clk) begin
    if (rst) begin
      s1  <= 1'b0;
      s2  <= 1'b0;
      s3  <= 1'b0;
      ack <= 1'b0;
    end else begin
      s1  <= uio_in[3];
      s2  <= s1;
      s3  <= s2;
      ack <= s2;                // rises on the clock edge that runs the command
    end
  end

  wire valid = mode ? uio_in[3] : (s2 & ~s3);

  wire signed [21:0] result;
  wire               ovf;
  wire        [5:0]  ptr;

  tiny_tpu_pe8 #(.N(32), .ACC(22)) u_pe (
      .clk    (clk),
      .rst    (rst),
      .valid  (valid),
      .cmd    (uio_in[2:0]),
      .din    (ui_in),
      .result (result),
      .ovf    (ovf),
      .ptr_out(ptr)
  );

  reg [7:0] byte_out;
  always @(*) begin
    case (uio_in[6:5])
      2'd0:    byte_out = result[7:0];
      2'd1:    byte_out = result[15:8];
      2'd2:    byte_out = {{2{result[21]}}, result[21:16]};
      default: byte_out = {1'b0, ptr, ovf};
    endcase
  end

  assign uo_out  = byte_out;
  assign uio_out = {ack, 7'b0000000};
  assign uio_oe  = 8'b1000_0000;               // uio[7] is an output, the rest inputs

  // List all unused inputs to prevent warnings
  wire _unused = &{ena, uio_in[7], 1'b0};

endmodule

`default_nettype wire
