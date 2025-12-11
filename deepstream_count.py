#!/usr/bin/env python3

import sys
import gi
import time
import math

gi.require_version('Gst', '1.0')
from gi.repository import GObject, Gst

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
        # Get the number of objects (fish) in the frame
        num_rects = frame_meta.num_obj_meta
        
        # 1. Print to Terminal
        print(f"Frame Number={frame_meta.frame_num} | Fish Count={num_rects}")

        # 2. Draw on Screen (OSD)
        display_meta = pyds.nvds_acquire_display_meta_from_pool(batch_meta)
        display_meta.num_labels = 1
        py_nvosd_text_params = display_meta.text_params[0]
        
        # Text content
        py_nvosd_text_params.display_text = f"Fish Count: {num_rects}"

        # Text position (Top Left)
        py_nvosd_text_params.x_offset = 20
        py_nvosd_text_params.y_offset = 20

        # Font settings
        py_nvosd_text_params.font_params.font_name = "Serif"
        py_nvosd_text_params.font_params.font_size = 24
        py_nvosd_text_params.font_params.font_color.set(1.0, 1.0, 1.0, 1.0) # White text

        # Background settings (Semi-transparent black box)
        py_nvosd_text_params.set_bg_clr = 1
        py_nvosd_text_params.text_bg_clr.set(0.0, 0.0, 0.0, 0.7) 

        pyds.nvds_add_display_meta_to_frame(frame_meta, display_meta)

        try:
            l_frame = l_frame.next
        except StopIteration:
            break
            
    return Gst.PadProbeReturn.OK

def main(args):
    # Check arguments
    if len(args) != 3:
        sys.stderr.write("Usage: python3 deepstream_count.py <h264_filename> <config_file>\n")
        sys.stderr.write("Example: python3 deepstream_count.py video.mp4 config_infer_primary_yoloV8_side.txt\n")
        sys.exit(1)

    video_file = args[1]
    config_file = args[2]

    # Standard GStreamer initialization
    GObject.threads_init()
    Gst.init(None)

    # Create Pipeline
    pipeline = Gst.Pipeline()

    # Create elements
    source = Gst.ElementFactory.make("filesrc", "file-source")
    h264parser = Gst.ElementFactory.make("h264parse", "h264-parser")
    decoder = Gst.ElementFactory.make("nvv4l2decoder", "nvv4l2-decoder")
    streammux = Gst.ElementFactory.make("nvstreammux", "Stream-muxer")
    pgie = Gst.ElementFactory.make("nvinfer", "primary-inference")
    nvvidconv = Gst.ElementFactory.make("nvvideoconvert", "convertor")
    nvosd = Gst.ElementFactory.make("nvdsosd", "onscreendisplay")
    
    # Use nveglglessink for Jetson display
    sink = Gst.ElementFactory.make("nveglglessink", "nvvideo-renderer")
    # If running headless (no monitor), use 'fakesink' instead:
    # sink = Gst.ElementFactory.make("fakesink", "fakesink")

    if not all([source, h264parser, decoder, streammux, pgie, nvvidconv, nvosd, sink]):
        sys.stderr.write(" One or more elements could not be created \n")
        sys.exit(1)

    # Set properties
    source.set_property('location', video_file)
    streammux.set_property('width', 1920)
    streammux.set_property('height', 1080)
    streammux.set_property('batch-size', 1)
    # Timeout needed for file sources
    streammux.set_property('batched-push-timeout', 4000000) 
    pgie.set_property('config-file-path', config_file)

    # Add elements to pipeline
    pipeline.add(source)
    pipeline.add(h264parser)
    pipeline.add(decoder)
    pipeline.add(streammux)
    pipeline.add(pgie)
    pipeline.add(nvvidconv)
    pipeline.add(nvosd)
    pipeline.add(sink)

    # Link elements
    source.link(h264parser)
    h264parser.link(decoder)

    sinkpad = streammux.get_request_pad("sink_0")
    srcpad = decoder.get_static_pad("src")
    srcpad.link(sinkpad)
    
    streammux.link(pgie)
    pgie.link(nvvidconv)
    nvvidconv.link(nvosd)
    nvosd.link(sink)

    # Add probe to OSD sink pad to count objects
    osdsinkpad = nvosd.get_static_pad("sink")
    osdsinkpad.add_probe(Gst.PadProbeType.BUFFER, osd_sink_pad_buffer_probe, 0)

    # Start pipeline
    loop = GObject.MainLoop()
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
