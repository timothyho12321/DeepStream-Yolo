#!/usr/bin/env python3

import sys
import gi
import time
import math
import os
from collections import deque
from collections import Counter
import threading
import csv
from datetime import datetime

gi.require_version('Gst', '1.0')
from gi.repository import GObject, Gst, GLib

# --- ARAVIS 0.8 CHECK ---
try:
    gi.require_version('Aravis', '0.8')
    from gi.repository import Aravis
    ARAVIS_AVAILABLE = True
    print("[INFO] Aravis 0.8 library loaded.")
except (ImportError, ValueError):
    print("[WARN] Aravis 0.8 not found. GigE camera support may be limited.")
    ARAVIS_AVAILABLE = False

try:
    import pyds
except ImportError:
    sys.stderr.write("Error: pyds module not found. Make sure DeepStream Python bindings are installed.\n")
    sys.exit(1)

# --- CONFIGURATION ---
# Can be a local file path, an RTSP URL (rtsp://...), or a Camera ID (e.g. Hikrobot-...)
# TOP_SOURCE = "Top_view_normal_20min_normal_lens_3_h264.mp4"
# SIDE_SOURCE = "Side_view_normal_20min_wide_lens_3_h264.mp4"
TOP_SOURCE= "Hikrobot-MV-CS023-10GC-DA7235770"  # Top view GigE camera (Replace with actual Device ID)
SIDE_SOURCE= "Hikrobot-MV-CS023-10GC-DA7235740"   # Side view GigE camera (Replace with actual Device ID)

TOP_CONFIG = "config_infer_primary_yoloV8.txt"
SIDE_CONFIG = "config_infer_primary_yoloV8_side.txt"

# --- VIEW SETTINGS (MANUAL ADJUSTMENT) ---
# Width: 960
# Height: 540 (Calculated as 960 / 1.77 to preserve aspect ratio)
VIEW_WIDTH = 960
VIEW_HEIGHT = 540

# CSV File Setup
CSV_DIR = "csv"
os.makedirs(CSV_DIR, exist_ok=True)

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
TOP_CSV_FILE = os.path.join(CSV_DIR, f"top_view_counts_{timestamp}.csv")
SIDE_CSV_FILE = os.path.join(CSV_DIR, f"side_view_counts_{timestamp}.csv")

with open(TOP_CSV_FILE, 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['Frame', 'Current_Count', 'Stabilized_Count'])

with open(SIDE_CSV_FILE, 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['Frame', 'Current_Count', 'Stabilized_Count'])

print(f"[OK] CSV files created: {TOP_CSV_FILE}, {SIDE_CSV_FILE}")

# --- STABILIZATION SETTINGS ---
FPS = 30
# Increased to 30 seconds to bridge gaps during heavy occlusion
WINDOW_SECONDS = 30
BUFFER_SIZE = FPS * WINDOW_SECONDS

# Global State
top_buffer = deque(maxlen=BUFFER_SIZE)
side_buffer = deque(maxlen=BUFFER_SIZE)

top_stats = {"current": 0, "stabilized": 0}
side_stats = {"current": 0, "stabilized": 0}

stats_lock = threading.Lock()

# --- ALGORITHM: 95th Percentile ---
def get_stabilized_count(buffer):
    if len(buffer) == 0:
        return 0

    sorted_buffer = sorted(buffer)

    # 95th Percentile Strategy
    # This filters out the top 5% of data (ignoring noise like 21 or 22)
    # But ensures we capture the "true max" (20) even if it only appears ~10-15% of the time.
    index = int(len(sorted_buffer) * 0.95)

    if index >= len(sorted_buffer):
        index = len(sorted_buffer) - 1

    return sorted_buffer[index]

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
            cur = top_stats["current"]
            stab = top_stats["stabilized"]

        print(f"[TOP] Frame={frame_meta.frame_num} | Current={cur} | Stabilized(95%)={stab}")

        with open(TOP_CSV_FILE, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([frame_meta.frame_num, cur, stab])

        display_meta = pyds.nvds_acquire_display_meta_from_pool(batch_meta)
        display_meta.num_labels = 1
        params = display_meta.text_params[0]
        params.display_text = f"TOP VIEW\nCurrent: {cur}\nStabilized: {stab}"
        params.x_offset = 20
        params.y_offset = 20
        params.font_params.font_name = "Serif"
        params.font_params.font_size = 20
        params.font_params.font_color.set(1.0, 1.0, 1.0, 1.0)
        params.set_bg_clr = 1
        params.text_bg_clr.set(0.0, 0.0, 0.0, 0.7)
        pyds.nvds_add_display_meta_to_frame(frame_meta, display_meta)

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
            cur = side_stats["current"]
            stab = side_stats["stabilized"]

        print(f"[SIDE] Frame={frame_meta.frame_num} | Current={cur} | Stabilized(95%)={stab}")

        with open(SIDE_CSV_FILE, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([frame_meta.frame_num, cur, stab])

        display_meta = pyds.nvds_acquire_display_meta_from_pool(batch_meta)
        display_meta.num_labels = 1
        params = display_meta.text_params[0]
        params.display_text = f"SIDE VIEW\nCurrent: {cur}\nStabilized: {stab}"
        params.x_offset = 20
        params.y_offset = 20
        params.font_params.font_name = "Serif"
        params.font_params.font_size = 20
        params.font_params.font_color.set(1.0, 1.0, 1.0, 1.0)
        params.set_bg_clr = 1
        params.text_bg_clr.set(0.0, 0.0, 0.0, 0.7)
        pyds.nvds_add_display_meta_to_frame(frame_meta, display_meta)

        try:
            l_frame = l_frame.next
        except StopIteration:
            break

    return Gst.PadProbeReturn.OK

# --- SOURCE CREATION HELPER ---
def create_source_bin(source_id, bin_name):
    """
    Creates a source bin based on the source_id.
    Supports:
    1. RTSP/HTTP URIs (via uridecodebin)
    2. Local Files (via uridecodebin)
    3. Camera IDs (via aravissrc for GigE, or v4l2src fallback)
    
    Returns: (GstElement, needs_pad_callback)
    """
    # 1. Check for URI (RTSP/HTTP)
    if "://" in source_id:
        print(f"[INFO] Creating URI source for: {source_id}")
        source = Gst.ElementFactory.make("uridecodebin", bin_name)
        source.set_property("uri", source_id)
        return source, True

    # 2. Check for Local File
    if os.path.exists(source_id):
        abs_path = os.path.abspath(source_id)
        uri = "file://" + abs_path
        print(f"[INFO] Creating File source for: {uri}")
        source = Gst.ElementFactory.make("uridecodebin", bin_name)
        source.set_property("uri", uri)
        return source, True

    # 3. Assume Camera ID (GigE/Aravis)
    print(f"[INFO] Source '{source_id}' not a file/URI. Attempting 'aravissrc' (GigE)...")
    
    if not ARAVIS_AVAILABLE:
        print("[WARN] Aravis 0.8 bindings missing. Ensure 'gir1.2-aravis-0.8' is installed.")

    # Create a Bin to encapsulate aravissrc -> videoconvert -> nvvideoconvert -> capsfilter
    bin_obj = Gst.Bin.new(bin_name)
    if not bin_obj:
        sys.stderr.write(" Unable to create bin \n")
        return None, False

    # Source: aravissrc
    src = Gst.ElementFactory.make("aravissrc", "src")
    if not src:
        sys.stderr.write(" Error: 'aravissrc' not found. Install gstreamer1.0-aravis.\n")
        return None, False
    src.set_property("camera-name", source_id)
    # Optional: Set exposure if needed
    # src.set_property("exposure", 20000.0) 

    # Converter 1: videoconvert (Handles Bayer -> RGB if needed)
    conv1 = Gst.ElementFactory.make("videoconvert", "conv1")
    
    # Converter 2: nvvideoconvert (Uploads to NVMM)
    nvconv = Gst.ElementFactory.make("nvvideoconvert", "nvconv")
    
    # Caps: Ensure NV12 for DeepStream
    caps = Gst.ElementFactory.make("capsfilter", "caps")
    caps.set_property("caps", Gst.Caps.from_string("video/x-raw(memory:NVMM), format=NV12"))

    Gst.Bin.add(bin_obj, src)
    Gst.Bin.add(bin_obj, conv1)
    Gst.Bin.add(bin_obj, nvconv)
    Gst.Bin.add(bin_obj, caps)

    # Link elements
    if not src.link(conv1):
        sys.stderr.write(" Failed to link aravissrc -> videoconvert\n")
        return None, False
    if not conv1.link(nvconv):
        sys.stderr.write(" Failed to link videoconvert -> nvvideoconvert\n")
        return None, False
    if not nvconv.link(caps):
        sys.stderr.write(" Failed to link nvvideoconvert -> capsfilter\n")
        return None, False

    # Create Ghost Pad
    pad = caps.get_static_pad("src")
    ghost_pad = Gst.GhostPad.new("src", pad)
    bin_obj.add_pad(ghost_pad)

    return bin_obj, False

# --- CALLBACK ---
def cb_newpad(decodebin, decoder_src_pad, data):
    caps = decoder_src_pad.get_current_caps()
    gststruct = caps.get_structure(0)
    gstname = gststruct.get_name()
    streammux = data

    if gstname.find("video") != -1:
        sinkpad = streammux.request_pad_simple("sink_0")
        if not sinkpad:
            sys.stderr.write(f"Error: Unable to get sink_0 pad from {streammux.get_name()}\n")
            return
        if decoder_src_pad.link(sinkpad) != Gst.PadLinkReturn.OK:
            sys.stderr.write(f"Error: Failed to link decoder to {streammux.get_name()}\n")
        else:
            print(f"Success: Linked {decodebin.get_name()} to {streammux.get_name()}")

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

def main():
    Gst.init(None)
    pipeline = Gst.Pipeline()

    # --- TOP BRANCH ---
    top_source, top_needs_cb = create_source_bin(TOP_SOURCE, "top-source")
    if not top_source:
        sys.stderr.write("Failed to create top source\n")
        sys.exit(1)

    top_mux = Gst.ElementFactory.make("nvstreammux", "top-mux")
    top_mux.set_property('width', 1920)
    top_mux.set_property('height', 1080)
    top_mux.set_property('batch-size', 1)
    top_infer = Gst.ElementFactory.make("nvinfer", "top-infer")
    top_infer.set_property('config-file-path', TOP_CONFIG)
    top_conv = Gst.ElementFactory.make("nvvideoconvert", "top-conv")
    top_osd = Gst.ElementFactory.make("nvdsosd", "top-osd")
    top_osd.set_property('display-clock', 0)

    # --- SIDE BRANCH ---
    side_source, side_needs_cb = create_source_bin(SIDE_SOURCE, "side-source")
    if not side_source:
        sys.stderr.write("Failed to create side source\n")
        sys.exit(1)

    side_mux = Gst.ElementFactory.make("nvstreammux", "side-mux")
    side_mux.set_property('width', 1920)
    side_mux.set_property('height', 1080)
    side_mux.set_property('batch-size', 1)
    side_infer = Gst.ElementFactory.make("nvinfer", "side-infer")
    side_infer.set_property('config-file-path', SIDE_CONFIG)
    side_conv = Gst.ElementFactory.make("nvvideoconvert", "side-conv")
    side_osd = Gst.ElementFactory.make("nvdsosd", "side-osd")
    side_osd.set_property('display-clock', 0)

    # --- COMPOSITION ---
    compositor = Gst.ElementFactory.make("nvcompositor", "compositor")
    sink = Gst.ElementFactory.make("nveglglessink", "nvvideo-renderer")
    sink.set_property("sync", 0)

    if not all([top_source, top_mux, top_infer, top_conv, top_osd,
                side_source, side_mux, side_infer, side_conv, side_osd,
                compositor, sink]):
        sys.stderr.write("Elements could not be created\n")
        sys.exit(1)

    for elem in [top_source, top_mux, top_infer, top_conv, top_osd,
                 side_source, side_mux, side_infer, side_conv, side_osd,
                 compositor, sink]:
        pipeline.add(elem)

    # --- LINKING SOURCES ---
    # Top Source Linking
    if top_needs_cb:
        top_source.connect("pad-added", cb_newpad, top_mux)
    else:
        sinkpad = top_mux.request_pad_simple("sink_0")
        srcpad = top_source.get_static_pad("src")
        if not srcpad.link(sinkpad) == Gst.PadLinkReturn.OK:
             sys.stderr.write("Failed to link top source to mux\n")
             sys.exit(1)

    # Side Source Linking
    if side_needs_cb:
        side_source.connect("pad-added", cb_newpad, side_mux)
    else:
        sinkpad = side_mux.request_pad_simple("sink_0")
        srcpad = side_source.get_static_pad("src")
        if not srcpad.link(sinkpad) == Gst.PadLinkReturn.OK:
             sys.stderr.write("Failed to link side source to mux\n")
             sys.exit(1)

    top_mux.link(top_infer)
    top_infer.link(top_conv)
    top_conv.link(top_osd)

    side_mux.link(side_infer)
    side_infer.link(side_conv)
    side_conv.link(side_osd)

    # --- ADJUSTED COMPOSITOR LINKING ---
    # We use the VIEW_WIDTH and VIEW_HEIGHT constants to ensure the ratio is correct

    # 1. Top View (Left)
    t_pad = top_osd.get_static_pad("src")
    comp_pad_0 = compositor.request_pad_simple("sink_%u")
    comp_pad_0.set_property("xpos", 0)
    comp_pad_0.set_property("ypos", 0)
    comp_pad_0.set_property("width", VIEW_WIDTH)
    comp_pad_0.set_property("height", VIEW_HEIGHT)
    t_pad.link(comp_pad_0)

    # 2. Side View (Right)
    s_pad = side_osd.get_static_pad("src")
    comp_pad_1 = compositor.request_pad_simple("sink_%u")
    comp_pad_1.set_property("xpos", VIEW_WIDTH) # Place immediately after Top view
    comp_pad_1.set_property("ypos", 0)
    comp_pad_1.set_property("width", VIEW_WIDTH)
    comp_pad_1.set_property("height", VIEW_HEIGHT)
    s_pad.link(comp_pad_1)

    compositor.link(sink)

    top_osd_sink = top_osd.get_static_pad("sink")
    top_osd_sink.add_probe(Gst.PadProbeType.BUFFER, top_infer_src_probe, 0)
    side_osd_sink = side_osd.get_static_pad("sink")
    side_osd_sink.add_probe(Gst.PadProbeType.BUFFER, side_infer_src_probe, 0)

    loop = GLib.MainLoop()
    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect ("message", bus_call, loop)

    print("Starting Dual View Pipeline with 95th Percentile Stabilization (30s Window)...")
    pipeline.set_state(Gst.State.PLAYING)
    try:
        loop.run()
    except:
        pass
    pipeline.set_state(Gst.State.NULL)

if __name__ == '__main__':
    main()