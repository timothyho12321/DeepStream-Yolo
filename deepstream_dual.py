#!/usr/bin/env python3

import sys
import gi
import time
import math
import os
from collections import deque
from collections import Counter
import threading

gi.require_version('Gst', '1.0')
from gi.repository import GObject, Gst, GLib

try:
    import pyds
except ImportError:
    sys.stderr.write("Error: pyds module not found. Make sure DeepStream Python bindings are installed.\n")
    sys.exit(1)

# --- CONFIGURATION ---
TOP_VIDEO_URI = "file:///home/flotech/DeepStream-Yolo/Top_view_normal_20min_wide_lens_3_h264.mp4"
SIDE_VIDEO_URI = "file:///home/flotech/DeepStream-Yolo/Side_view_normal_20min_wide_lens_3_h264.mp4"

TOP_CONFIG = "config_infer_primary_yoloV8.txt"
SIDE_CONFIG = "config_infer_primary_yoloV8_side.txt"

# Stabilization Settings
FPS = 30
WINDOW_SECONDS = 10
BUFFER_SIZE = FPS * WINDOW_SECONDS

# Global State
top_buffer = deque(maxlen=BUFFER_SIZE)
side_buffer = deque(maxlen=BUFFER_SIZE)

top_stats = {"current": 0, "stabilized": 0}
side_stats = {"current": 0, "stabilized": 0}

# Lock for thread safety if needed (GStreamer callbacks can be concurrent)
stats_lock = threading.Lock()

def get_stabilized_count(buffer):
    if len(buffer) == 0:
        return 0
    counts = Counter(buffer)
    return counts.most_common(1)[0][0]

# --- PROBES ---

def top_infer_src_probe(pad, info, u_data):
    gst_buffer = info.get_buffer()
    if not gst_buffer:
        return Gst.PadProbeReturn.OK

    batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
    l_frame = batch_meta.frame_meta_list

    while l_frame is not None:
        try:
            frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
        except StopIteration:
            break

        num_rects = frame_meta.num_obj_meta
        
        with stats_lock:
            top_buffer.append(num_rects)
            top_stats["current"] = num_rects
            top_stats["stabilized"] = get_stabilized_count(top_buffer)
            
            # Print to terminal
            print(f"[TOP] Frame={frame_meta.frame_num} | Cur={num_rects} | Stab={top_stats['stabilized']}")

        try:
            l_frame = l_frame.next
        except StopIteration:
            break
            
    return Gst.PadProbeReturn.OK

def side_infer_src_probe(pad, info, u_data):
    gst_buffer = info.get_buffer()
    if not gst_buffer:
        return Gst.PadProbeReturn.OK

    batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
    l_frame = batch_meta.frame_meta_list

    while l_frame is not None:
        try:
            frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
        except StopIteration:
            break

        num_rects = frame_meta.num_obj_meta
        
        with stats_lock:
            side_buffer.append(num_rects)
            side_stats["current"] = num_rects
            side_stats["stabilized"] = get_stabilized_count(side_buffer)
            
            # Print to terminal
            print(f"[SIDE] Frame={frame_meta.frame_num} | Cur={num_rects} | Stab={side_stats['stabilized']}")

        try:
            l_frame = l_frame.next
        except StopIteration:
            break
            
    return Gst.PadProbeReturn.OK

def osd_sink_pad_probe(pad, info, u_data):
    # This probe draws the text overlay on the final composed image
    gst_buffer = info.get_buffer()
    if not gst_buffer:
        return Gst.PadProbeReturn.OK

    batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
    l_frame = batch_meta.frame_meta_list

    while l_frame is not None:
        try:
            frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
        except StopIteration:
            break

        # Acquire display meta
        display_meta = pyds.nvds_acquire_display_meta_from_pool(batch_meta)
        display_meta.num_labels = 2 # Two labels (Left and Right)

        with stats_lock:
            t_cur = top_stats["current"]
            t_stab = top_stats["stabilized"]
            s_cur = side_stats["current"]
            s_stab = side_stats["stabilized"]

        # --- LEFT LABEL (TOP VIEW) ---
        params_top = display_meta.text_params[0]
        params_top.display_text = f"TOP VIEW\nCurrent: {t_cur}\nStabilized: {t_stab}"
        params_top.x_offset = 20
        params_top.y_offset = 20
        params_top.font_params.font_name = "Serif"
        params_top.font_params.font_size = 20
        params_top.font_params.font_color.set(1.0, 1.0, 1.0, 1.0)
        params_top.set_bg_clr = 1
        params_top.text_bg_clr.set(0.0, 0.0, 0.0, 0.7)

        # --- RIGHT LABEL (SIDE VIEW) ---
        params_side = display_meta.text_params[1]
        params_side.display_text = f"SIDE VIEW\nCurrent: {s_cur}\nStabilized: {s_stab}"
        # Position on the right half (assuming 1920 width, start at 980)
        params_side.x_offset = 980 
        params_side.y_offset = 20
        params_side.font_params.font_name = "Serif"
        params_side.font_params.font_size = 20
        params_side.font_params.font_color.set(1.0, 1.0, 1.0, 1.0)
        params_side.set_bg_clr = 1
        params_side.text_bg_clr.set(0.0, 0.0, 0.0, 0.7)

        pyds.nvds_add_display_meta_to_frame(frame_meta, display_meta)

        try:
            l_frame = l_frame.next
        except StopIteration:
            break

    return Gst.PadProbeReturn.OK

def cb_newpad(decodebin, decoder_src_pad, data):
    caps = decoder_src_pad.get_current_caps()
    gststruct = caps.get_structure(0)
    gstname = gststruct.get_name()
    streammux = data
    if gstname.find("video") != -1:
        sinkpad = streammux.get_request_pad("sink_0")
        if not sinkpad:
            sys.stderr.write("Unable to get the sink pad of streammux \n")
        decoder_src_pad.link(sinkpad)

def main():
    Gst.init(None)
    pipeline = Gst.Pipeline()

    # --- TOP VIEW BRANCH ---
    top_source = Gst.ElementFactory.make("uridecodebin", "top-source")
    top_source.set_property("uri", TOP_VIDEO_URI)
    
    top_mux = Gst.ElementFactory.make("nvstreammux", "top-mux")
    top_mux.set_property('width', 1920)
    top_mux.set_property('height', 1080)
    top_mux.set_property('batch-size', 1)
    
    top_infer = Gst.ElementFactory.make("nvinfer", "top-infer")
    top_infer.set_property('config-file-path', TOP_CONFIG)
    
    top_conv = Gst.ElementFactory.make("nvvideoconvert", "top-conv")

    # --- SIDE VIEW BRANCH ---
    side_source = Gst.ElementFactory.make("uridecodebin", "side-source")
    side_source.set_property("uri", SIDE_VIDEO_URI)
    
    side_mux = Gst.ElementFactory.make("nvstreammux", "side-mux")
    side_mux.set_property('width', 1920)
    side_mux.set_property('height', 1080)
    side_mux.set_property('batch-size', 1)
    
    side_infer = Gst.ElementFactory.make("nvinfer", "side-infer")
    side_infer.set_property('config-file-path', SIDE_CONFIG)
    
    side_conv = Gst.ElementFactory.make("nvvideoconvert", "side-conv")

    # --- COMPOSITION & OUTPUT ---
    compositor = Gst.ElementFactory.make("nvmultistreamtiler", "compositor")
    # Tiler properties to make it side-by-side
    compositor.set_property('rows', 1)
    compositor.set_property('columns', 2)
    compositor.set_property('width', 1920) # Output width
    compositor.set_property('height', 540) # Output height (scaled down to fit side by side? or 1920x1080 canvas?)
    # Actually, nvmultistreamtiler scales inputs to fit the grid.
    # If we want 1920x1080 total output with 2 streams side-by-side:
    # Each stream becomes 960x540 (if aspect ratio maintained) or 960x1080 (stretched).
    # Let's set output to 1920x540 to maintain aspect ratio of 16:9 inputs side-by-side?
    # Or 3840x1080?
    # Let's try standard 1920x1080 output. The tiler will scale them to 960x540 each and center them or fill.
    # Better: Use nvcompositor for precise control, but tiler is easier.
    # Let's stick to tiler for simplicity.
    compositor.set_property('width', 1920)
    compositor.set_property('height', 1080)
    
    nvosd = Gst.ElementFactory.make("nvdsosd", "onscreendisplay")
    sink = Gst.ElementFactory.make("nveglglessink", "nvvideo-renderer")

    if not all([top_source, top_mux, top_infer, top_conv, 
                side_source, side_mux, side_infer, side_conv, 
                compositor, nvosd, sink]):
        sys.stderr.write("Elements could not be created\n")
        sys.exit(1)

    # --- ADD ELEMENTS ---
    pipeline.add(top_source)
    pipeline.add(top_mux)
    pipeline.add(top_infer)
    pipeline.add(top_conv)
    
    pipeline.add(side_source)
    pipeline.add(side_mux)
    pipeline.add(side_infer)
    pipeline.add(side_conv)
    
    pipeline.add(compositor)
    pipeline.add(nvosd)
    pipeline.add(sink)

    # --- LINKING ---
    # Dynamic linking for sources
    top_source.connect("pad-added", cb_newpad, top_mux)
    side_source.connect("pad-added", cb_newpad, side_mux)

    # Top Branch
    top_mux.link(top_infer)
    top_infer.link(top_conv)
    
    # Side Branch
    side_mux.link(side_infer)
    side_infer.link(side_conv)

    # Link to Compositor (Tiler)
    # Tiler has sink pads sink_0, sink_1, etc.
    
    # Link Top Conv -> Tiler Sink 0
    t_pad = top_conv.get_static_pad("src")
    tile_pad_0 = compositor.get_request_pad("sink_0")
    t_pad.link(tile_pad_0)

    # Link Side Conv -> Tiler Sink 1
    s_pad = side_conv.get_static_pad("src")
    tile_pad_1 = compositor.get_request_pad("sink_1")
    s_pad.link(tile_pad_1)

    # Output Chain
    compositor.link(nvosd)
    nvosd.link(sink)

    # --- PROBES ---
    # Probe 1: Top Infer Src (Count Top Fish)
    top_infer_src = top_infer.get_static_pad("src")
    top_infer_src.add_probe(Gst.PadProbeType.BUFFER, top_infer_src_probe, 0)

    # Probe 2: Side Infer Src (Count Side Fish)
    side_infer_src = side_infer.get_static_pad("src")
    side_infer_src.add_probe(Gst.PadProbeType.BUFFER, side_infer_src_probe, 0)

    # Probe 3: OSD Sink (Draw Text)
    osd_sink = nvosd.get_static_pad("sink")
    osd_sink.add_probe(Gst.PadProbeType.BUFFER, osd_sink_pad_probe, 0)

    # --- RUN ---
    loop = GLib.MainLoop()
    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect ("message", bus_call, loop)

    print("Starting Dual View Pipeline...")
    pipeline.set_state(Gst.State.PLAYING)
    try:
        loop.run()
    except:
        pass
    pipeline.set_state(Gst.State.NULL)

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

if __name__ == '__main__':
    main()
