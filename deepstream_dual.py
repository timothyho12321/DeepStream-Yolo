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

try:
    import pyds
except ImportError:
    sys.stderr.write("Error: pyds module not found. Make sure DeepStream Python bindings are installed.\n")
    sys.exit(1)

# --- CONFIGURATION ---
TOP_VIDEO_FILENAME = "Top_view_normal_20min_normal_lens_3_h264.mp4"
SIDE_VIDEO_FILENAME = "Side_view_normal_20min_wide_lens_3_h264.mp4"

# Ensure these config files exist or point to valid YOLO configs
TOP_CONFIG = "config_infer_primary_yoloV8.txt"
SIDE_CONFIG = "config_infer_primary_yoloV8_side.txt"

# --- FILE CHECKER ---
def check_file(filename):
    current_dir = os.getcwd()
    path = os.path.join(current_dir, filename)
    if not os.path.exists(path):
        print(f"\n[ERROR] File not found: {path}")
        print("Please check the filename and ensure it is in the same folder as this script.\n")
        sys.exit(1)
    return "file://" + path

# Validate files
print(f"Checking for files in: {os.getcwd()}")
TOP_VIDEO_URI = check_file(TOP_VIDEO_FILENAME)
SIDE_VIDEO_URI = check_file(SIDE_VIDEO_FILENAME)
print("[OK] Video files found.")

# CSV File Setup with timestamp
CSV_DIR = "csv"
os.makedirs(CSV_DIR, exist_ok=True)

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
TOP_CSV_FILE = os.path.join(CSV_DIR, f"top_view_counts_{timestamp}.csv")
SIDE_CSV_FILE = os.path.join(CSV_DIR, f"side_view_counts_{timestamp}.csv")

# Initialize CSV files with headers
with open(TOP_CSV_FILE, 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['Frame', 'Current_Count', 'Stabilized_Count'])

with open(SIDE_CSV_FILE, 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['Frame', 'Current_Count', 'Stabilized_Count'])

print(f"[OK] CSV files created: {TOP_CSV_FILE}, {SIDE_CSV_FILE}")

# Stabilization Settings
FPS = 30
WINDOW_SECONDS = 10
BUFFER_SIZE = FPS * WINDOW_SECONDS

# Global State
top_buffer = deque(maxlen=BUFFER_SIZE)
side_buffer = deque(maxlen=BUFFER_SIZE)

top_stats = {"current": 0, "stabilized": 0}
side_stats = {"current": 0, "stabilized": 0}

stats_lock = threading.Lock()

# --- NEW ALGORITHM: 95th Percentile ---
def get_stabilized_count(buffer):
    if len(buffer) == 0:
        return 0

    # Sort the buffer to organize counts from low to high
    sorted_buffer = sorted(buffer)

    # Calculate the index for the 95th percentile
    # This ignores the bottom 95% (undercounts due to occlusion)
    # and ignores the top 5% (potential overcounts/noise)
    index = int(len(sorted_buffer) * 0.95)

    # Ensure index is within valid bounds
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

        # 1. Update Stats
        num_rects = frame_meta.num_obj_meta
        with stats_lock:
            top_buffer.append(num_rects)
            top_stats["current"] = num_rects
            top_stats["stabilized"] = get_stabilized_count(top_buffer)
            cur = top_stats["current"]
            stab = top_stats["stabilized"]

        # Print to Terminal
        print(f"[TOP] Frame={frame_meta.frame_num} | Current={cur} | Stabilized(95%)={stab}")

        # Write to CSV
        with open(TOP_CSV_FILE, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([frame_meta.frame_num, cur, stab])

        # 2. Draw Text Immediately
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

        # 1. Update Stats
        num_rects = frame_meta.num_obj_meta
        with stats_lock:
            side_buffer.append(num_rects)
            side_stats["current"] = num_rects
            side_stats["stabilized"] = get_stabilized_count(side_buffer)
            cur = side_stats["current"]
            stab = side_stats["stabilized"]

        # Print to Terminal
        print(f"[SIDE] Frame={frame_meta.frame_num} | Current={cur} | Stabilized(95%)={stab}")

        # Write to CSV
        with open(SIDE_CSV_FILE, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([frame_meta.frame_num, cur, stab])

        # 2. Draw Text Immediately
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

    # ==========================
    # 1. TOP VIEW BRANCH
    # ==========================
    top_source = Gst.ElementFactory.make("uridecodebin", "top-source")
    top_source.set_property("uri", TOP_VIDEO_URI)

    top_mux = Gst.ElementFactory.make("nvstreammux", "top-mux")
    top_mux.set_property('width', 1920)
    top_mux.set_property('height', 1080)
    top_mux.set_property('batch-size', 1)

    top_infer = Gst.ElementFactory.make("nvinfer", "top-infer")
    top_infer.set_property('config-file-path', TOP_CONFIG)

    top_conv = Gst.ElementFactory.make("nvvideoconvert", "top-conv")
    top_osd = Gst.ElementFactory.make("nvdsosd", "top-osd")
    top_osd.set_property('display-clock', 0)

    # ==========================
    # 2. SIDE VIEW BRANCH
    # ==========================
    side_source = Gst.ElementFactory.make("uridecodebin", "side-source")
    side_source.set_property("uri", SIDE_VIDEO_URI)

    side_mux = Gst.ElementFactory.make("nvstreammux", "side-mux")
    side_mux.set_property('width', 1920)
    side_mux.set_property('height', 1080)
    side_mux.set_property('batch-size', 1)

    side_infer = Gst.ElementFactory.make("nvinfer", "side-infer")
    side_infer.set_property('config-file-path', SIDE_CONFIG)

    side_conv = Gst.ElementFactory.make("nvvideoconvert", "side-conv")
    side_osd = Gst.ElementFactory.make("nvdsosd", "side-osd")
    side_osd.set_property('display-clock', 0)

    # ==========================
    # 3. COMPOSITION
    # ==========================
    compositor = Gst.ElementFactory.make("nvcompositor", "compositor")
    sink = Gst.ElementFactory.make("nveglglessink", "nvvideo-renderer")
    sink.set_property("sync", 0)

    # Validate Elements
    if not all([top_source, top_mux, top_infer, top_conv, top_osd,
                side_source, side_mux, side_infer, side_conv, side_osd,
                compositor, sink]):
        sys.stderr.write("Elements could not be created\n")
        sys.exit(1)

    # Add Elements
    for elem in [top_source, top_mux, top_infer, top_conv, top_osd,
                 side_source, side_mux, side_infer, side_conv, side_osd,
                 compositor, sink]:
        pipeline.add(elem)

    # --- DYNAMIC LINKING ---
    top_source.connect("pad-added", cb_newpad, top_mux)
    side_source.connect("pad-added", cb_newpad, side_mux)

    # --- STATIC LINKING ---
    top_mux.link(top_infer)
    top_infer.link(top_conv)
    top_conv.link(top_osd)

    side_mux.link(side_infer)
    side_infer.link(side_conv)
    side_conv.link(side_osd)

    # --- COMPOSITOR LINKING ---
    t_pad = top_osd.get_static_pad("src")
    comp_pad_0 = compositor.request_pad_simple("sink_%u")
    comp_pad_0.set_property("xpos", 0)
    comp_pad_0.set_property("ypos", 0)
    comp_pad_0.set_property("width", 960)
    comp_pad_0.set_property("height", 1080)
    t_pad.link(comp_pad_0)

    s_pad = side_osd.get_static_pad("src")
    comp_pad_1 = compositor.request_pad_simple("sink_%u")
    comp_pad_1.set_property("xpos", 960)
    comp_pad_1.set_property("ypos", 0)
    comp_pad_1.set_property("width", 960)
    comp_pad_1.set_property("height", 1080)
    s_pad.link(comp_pad_1)

    compositor.link(sink)

    # --- ATTACH PROBES ---
    top_osd_sink = top_osd.get_static_pad("sink")
    top_osd_sink.add_probe(Gst.PadProbeType.BUFFER, top_infer_src_probe, 0)

    side_osd_sink = side_osd.get_static_pad("sink")
    side_osd_sink.add_probe(Gst.PadProbeType.BUFFER, side_infer_src_probe, 0)

    # --- RUN ---
    loop = GLib.MainLoop()
    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect ("message", bus_call, loop)

    print("Starting Dual View Pipeline with 95th Percentile Stabilization...")
    pipeline.set_state(Gst.State.PLAYING)
    try:
        loop.run()
    except:
        pass
    pipeline.set_state(Gst.State.NULL)

if __name__ == '__main__':
    main()