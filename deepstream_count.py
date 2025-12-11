#!/usr/bin/env python3

import sys
import gi
import time
import math
import os

gi.require_version('Gst', '1.0')
from gi.repository import GObject, Gst, GLib

try:
    import pyds
except ImportError:
    sys.stderr.write("Error: pyds module not found. Make sure DeepStream Python bindings are installed.\n")
    sys.exit(1)

def bus_call(bus, message, loop):
    t = message.type
    if t == Gst.MessageType.EOS:
        sys.stdout.write("End of stream\n")
        loop.quit()
    elif t == Gst.MessageType.ERROR:
        err, debug = message.parse_error()
        sys.stderr.write("Error: %s: %s\n" % (err, debug))
        loop.quit()
    return True

def osd_sink_pad_buffer_probe(pad, info, u_data):
    gst_buffer = info.get_buffer()
    if not gst_buffer:
        print("Unable to get GstBuffer")
        return Gst.PadProbeReturn.OK

    # Retrieve batch metadata from the gst_buffer
    batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
    l_frame = batch_meta.frame_meta_list

    while l_frame is not None:
        try:
            frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
        except StopIteration:
            break

        # --- COUNTING LOGIC ---
        num_rects = frame_meta.num_obj_meta

        # 1. Print to Terminal
        print(f"Frame Number={frame_meta.frame_num} | Fish Count={num_rects}")

        # 2. Draw on Screen (OSD)
        display_meta = pyds.nvds_acquire_display_meta_from_pool(batch_meta)
        display_meta.num_labels = 1
        py_nvosd_text_params = display_meta.text_params[0]

        py_nvosd_text_params.display_text = f"Fish Count: {num_rects}"
        py_nvosd_text_params.x_offset = 20
        py_nvosd_text_params.y_offset = 20
        py_nvosd_text_params.font_params.font_name = "Serif"
        py_nvosd_text_params.font_params.font_size = 24
        py_nvosd_text_params.font_params.font_color.set(1.0, 1.0, 1.0, 1.0)
        py_nvosd_text_params.set_bg_clr = 1
        py_nvosd_text_params.text_bg_clr.set(0.0, 0.0, 0.0, 0.7)

        pyds.nvds_add_display_meta_to_frame(frame_meta, display_meta)

        try:
            l_frame = l_frame.next
        except StopIteration:
            break

    return Gst.PadProbeReturn.OK

# Callback function to link uridecodebin to streammux
def cb_newpad(decodebin, decoder_src_pad, data):
    print("In cb_newpad\n")
    caps = decoder_src_pad.get_current_caps()
    gststruct = caps.get_structure(0)
    gstname = gststruct.get_name()
    streammux = data

    # We only want to link video streams (not audio)
    if gstname.find("video") != -1:
        # Get a sink pad from the streammux
        sinkpad = streammux.get_request_pad("sink_0")
        if not sinkpad:
            sys.stderr.write("Unable to get the sink pad of streammux \n")

        # Link the decoder pad to the streammux pad
        if decoder_src_pad.link(sinkpad) != Gst.PadLinkReturn.OK:
            print("Failed to link decoder src pad to streammux sink pad\n")
        else:
            print("Successfully linked uridecodebin to streammux\n")

def main(args):
    if len(args) != 3:
        sys.stderr.write("Usage: python3 deepstream_count.py <h264_filename> <config_file>\n")
        sys.exit(1)

    # Convert filename to absolute URI for uridecodebin
    video_file = args[1]
    if not video_file.startswith("file://"):
        video_file = "file://" + os.path.abspath(video_file)

    config_file = args[2]

    Gst.init(None)

    pipeline = Gst.Pipeline()

    # --- ELEMENT CREATION ---
    # REPLACED: filesrc+h264parse+decoder -> uridecodebin
    source_bin = Gst.ElementFactory.make("uridecodebin", "uri-decode-bin")

    streammux = Gst.ElementFactory.make("nvstreammux", "Stream-muxer")
    pgie = Gst.ElementFactory.make("nvinfer", "primary-inference")
    nvvidconv = Gst.ElementFactory.make("nvvideoconvert", "convertor")
    nvosd = Gst.ElementFactory.make("nvdsosd", "onscreendisplay")
    sink = Gst.ElementFactory.make("nveglglessink", "nvvideo-renderer")

    if not all([source_bin, streammux, pgie, nvvidconv, nvosd, sink]):
        sys.stderr.write(" One or more elements could not be created \n")
        sys.exit(1)

    # --- CONFIGURE ELEMENTS ---
    source_bin.set_property("uri", video_file)

    # Connect the dynamic pad callback
    source_bin.connect("pad-added", cb_newpad, streammux)

    streammux.set_property('width', 1920)
    streammux.set_property('height', 1080)
    streammux.set_property('batch-size', 1)

    # Important for file sources to avoid EOS handling issues
    # But usually 0 is safer for uridecodebin unless you have live rtsp
    # streammux.set_property('batched-push-timeout', 4000000)

    pgie.set_property('config-file-path', config_file)

    # --- ADD TO PIPELINE ---
    pipeline.add(source_bin)
    pipeline.add(streammux)
    pipeline.add(pgie)
    pipeline.add(nvvidconv)
    pipeline.add(nvosd)
    pipeline.add(sink)

    # --- LINK ELEMENTS ---
    # Note: source_bin is linked dynamically in cb_newpad

    streammux.link(pgie)
    pgie.link(nvvidconv)
    nvvidconv.link(nvosd)
    nvosd.link(sink)

    # --- PROBE ---
    osdsinkpad = nvosd.get_static_pad("sink")
    osdsinkpad.add_probe(Gst.PadProbeType.BUFFER, osd_sink_pad_buffer_probe, 0)

    # --- MAIN LOOP ---
    # Updated to GLib.MainLoop to fix deprecation warning
    loop = GLib.MainLoop()
    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect ("message", bus_call, loop)

    print("Starting pipeline...")
    pipeline.set_state(Gst.State.PLAYING)
    try:
        loop.run()
    except:
        pass

    pipeline.set_state(Gst.State.NULL)

if __name__ == '__main__':
    sys.exit(main(sys.argv))