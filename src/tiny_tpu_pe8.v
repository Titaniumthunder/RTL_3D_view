/*
 * Copyright (c) 2026 MAC Attack (Alex Salamati, James Manikyan, Marco Flores)
 * SPDX-License-Identifier: Apache-2.0
 *
 * tiny_tpu_pe8: Tiny-TPU v2 processing element. One signed INT8 multiply-accumulate with an
 * input buffer, so the host sends each layer's inputs ONCE and then only one weight byte per MAC.
 * Platform-independent: the Tiny Tapeout and FPGA tops only add pins / a bus around it.
 *
 * Why an input buffer (not a weight buffer): running one inference at a time, every weight is
 * used once, but every input is used by every neuron of the layer. Buffering the inputs halves
 * the bytes per MAC (1 instead of 2) and lets an INT8 MAC fit Tiny Tapeout's 8 input pins.
 *
 * Commands (executed on a clock edge with valid = 1; din is the data byte):
 *   0 NOP
 *   1 CLEAR   acc <= 0, ovf <= 0, ptr <= 0        start a neuron (or rewind before LOAD_X)
 *   2 LOAD_X  x[ptr] <= din, ptr <= ptr + 1       store the layer's inputs (up to N)
 *   3 MAC     acc <= acc + x[ptr] * din, ptr++    din is the weight for input x[ptr]
 *   4 READ    result <= acc                       a stable copy for the host to read out
 *   5-7       same as NOP (reserved)
 *
 * Numbers: x and w are signed 8-bit (INT4 models work too: -7..7 are valid INT8 values).
 * ACC = 22 bits, the same exact width as our FPGA array: |product| <= 128 * 128 = 2^14, so up to
 * 2^21 / 2^14 = 128 MACs per neuron can never overflow (a neuron has at most N = 32 inputs here).
 * If a sum still leaves the 22-bit range, ovf goes high until the next CLEAR (never silent).
 * ptr counts modulo N: a neuron with more than N inputs would wrap, so the host must not do that.
 */

`default_nettype none

module tiny_tpu_pe8 #(
    parameter N   = 32,     // input buffer depth = largest layer input this chip can run
    parameter ACC = 22      // accumulator bits
) (
    input  wire                  clk,
    input  wire                  rst,          // synchronous, active high
    input  wire                  valid,
    input  wire        [2:0]     cmd,
    input  wire        [7:0]     din,
    output reg  signed [ACC-1:0] result,
    output reg                   ovf,
    output wire        [5:0]     ptr_out       // buffer position (debug / status byte)
);

  localparam PW = (N <= 2) ? 1 : $clog2(N);
  localparam [2:0] C_CLEAR = 3'd1, C_LOAD = 3'd2, C_MAC = 3'd3, C_READ = 3'd4;

  reg signed [7:0]     x [0:N-1];
  reg        [PW-1:0]  ptr;
  reg signed [ACC-1:0] acc;

  wire signed [7:0]     xi      = x[ptr];
  wire signed [15:0]    product = xi * $signed(din);
  wire signed [ACC:0]   sum     = {acc[ACC-1], acc} + {{(ACC-15){product[15]}}, product};
  wire                  sum_ovf = sum[ACC] ^ sum[ACC-1];

  always @(posedge clk) begin
    if (rst) begin
      acc    <= {ACC{1'b0}};
      result <= {ACC{1'b0}};
      ovf    <= 1'b0;
      ptr    <= {PW{1'b0}};
    end else if (valid) begin
      case (cmd)
        C_CLEAR: begin
          acc <= {ACC{1'b0}};
          ovf <= 1'b0;
          ptr <= {PW{1'b0}};
        end
        C_LOAD: begin
          ptr <= ptr + 1'b1;
        end
        C_MAC: begin
          acc <= sum[ACC-1:0];
          if (sum_ovf) ovf <= 1'b1;
          ptr <= ptr + 1'b1;
        end
        C_READ: result <= acc;
        default: ;
      endcase
    end
  end

  // The buffer has no reset (it is always written before it is read), which keeps it small
  always @(posedge clk) begin
    if (valid && cmd == C_LOAD) x[ptr] <= din;
  end

  assign ptr_out = {{(6 - PW){1'b0}}, ptr};

endmodule

`default_nettype wire
