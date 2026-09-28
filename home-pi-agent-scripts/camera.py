"""
Camera device management for Bastet robot (Pi 5).
"""
import os, json, subprocess, time, urllib.request
from pathlib import Path
import state
from state import (
    GATEWAY_URL, API_TOKEN, ssl_ctx,
    CAMERA_MAPPING_FILE, CALIB_STATUS_FILE,
    CAMERA_CALIB_LEFT_FILE, CAMERA_CALIB_RIGHT_FILE, CAMERA_CALIB_MONO_FILE,
)

def _list_physical_usb_video_devices() -> list[str]:
    """Return sorted list of /dev/videoN paths bound to currently-plugged USB cameras.
    Excludes metadata nodes (video-index1).
    """
    out = set()
    by_id_dir = "/dev/v4l/by-id"
    if not os.path.isdir(by_id_dir):
        return []
    try:
        for entry in os.listdir(by_id_dir):
            if not entry.startswith("usb-"):
                continue
            # Filter metadata endpoints (not real cameras)
            if "index1" in entry or "metadata" in entry:
                continue
            link = os.path.join(by_id_dir, entry)
            try:
                real = os.path.realpath(link)
                if real.startswith("/dev/video"):
                    out.add(real)
            except OSError:
                continue
    except OSError:
        pass
    return sorted(out)

def get_camera_devices() -> dict:
    physical_devs = _list_physical_usb_video_devices()
    
    # Charger la configuration enregistrée si elle existe
    saved_left = None
    saved_right = None
    if CAMERA_MAPPING_FILE.exists():
        try:
            data = json.loads(CAMERA_MAPPING_FILE.read_text())
            saved_left = data.get("left")
            saved_right = data.get("right")
            if saved_left and isinstance(saved_left, dict):
                saved_left = saved_left.get("device")
            if saved_right and isinstance(saved_right, dict):
                saved_right = saved_right.get("device")
        except Exception:
            pass

    # Résoudre les périphériques réels
    left_dev = None
    right_dev = None
    
    if saved_left and os.path.exists(saved_left):
        left_dev = saved_left
    if saved_right and os.path.exists(saved_right):
        if saved_right != left_dev:
            right_dev = saved_right

    # Fallback/Auto-détection : si non défini ou non branché, on pioche dans les caméras physiques
    available = [d for d in physical_devs if d != left_dev and d != right_dev]
    
    if not left_dev:
        if available:
            left_dev = available.pop(0)
        else:
            left_dev = "/dev/video0"
            
    if not right_dev:
        if available:
            right_dev = available.pop(0)
        else:
            right_dev = "/dev/video2"

    default_mapping = {
        1: {"device": left_dev, "fingerprint": None},
        2: {"device": right_dev, "fingerprint": None}
    }
    
    for cam_id in [1, 2]:
        dev_info = default_mapping[cam_id]
        dev_info["fingerprint"] = get_camera_fingerprint(dev_info["device"])
        
    return default_mapping

def get_camera_fingerprint(device: str) -> str:
    try:
        r = subprocess.run(["udevadm", "info", "--query=property", "--name="+device], capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            props = {}
            for line in r.stdout.splitlines():
                if '=' in line: k, v = line.split('=', 1); props[k] = v
            serial = props.get('ID_SERIAL_SHORT', '')
            if serial: return serial
            vid = props.get('ID_VENDOR_ID', ''); pid = props.get('ID_MODEL_ID', '')
            if vid and pid: return f'{vid}:{pid}'
            devpath = props.get('DEVPATH', '')
            if devpath: return devpath
    except Exception: pass
    return device

def get_calibration_status() -> dict:
    default = {
        1: {"calibrated": True, "fingerprint": None},
        2: {"calibrated": True, "fingerprint": None}
    }
    if CALIB_STATUS_FILE.exists():
        try:
            data = json.loads(CALIB_STATUS_FILE.read_text())
            for cam_id in [1, 2]:
                if str(cam_id) in data: default[cam_id] = data[str(cam_id)]
                elif cam_id in data: default[cam_id] = data[cam_id]
        except Exception: pass
    return default

def save_calibration_status(cam_id: int, calibrated: bool, fingerprint: str = None):
    status = get_calibration_status()
    status[cam_id] = {"calibrated": calibrated, "fingerprint": fingerprint}
    try:
        CALIB_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        CALIB_STATUS_FILE.write_text(json.dumps({str(k): v for k, v in status.items()}))
    except Exception as e: print(f"[Agent] Erreur sauvegarde calib_status: {e}")

def detect_camera_change() -> dict:
    status = get_calibration_status(); mapping = get_camera_devices(); result = {}
    for cam_id in [1, 2]:
        dev_info = mapping.get(cam_id, {})
        current_fp = dev_info.get("fingerprint") if isinstance(dev_info, dict) else None
        saved_fp = status.get(cam_id, {}).get("fingerprint")
        changed = False
        if current_fp and saved_fp and current_fp != saved_fp:
            changed = True; save_calibration_status(cam_id, False, current_fp)
            print(f"[Agent] Camera {cam_id} changed. Calibration invalidated.")
        elif current_fp and not saved_fp:
            save_calibration_status(cam_id, status.get(cam_id, {}).get("calibrated", False), current_fp)
        result[cam_id] = changed
    return result

def get_active_video_devices() -> list:
    fallback = ['/dev/video0', '/dev/video2']; out = set()
    try:
        if os.path.isdir('/dev/v4l/by-id'):
            for entry in os.listdir('/dev/v4l/by-id'):
                if not entry.startswith('usb-'): continue
                if 'index1' in entry or 'metadata' in entry: continue
                link = os.path.join('/dev/v4l/by-id', entry)
                try:
                    real = os.path.realpath(link)
                    if real.startswith('/dev/video'): out.add(real)
                except OSError: continue
    except Exception: pass
    return sorted(out) if out else sorted(set(fallback))

def _json_calib_to_yaml(calib: dict) -> str:
    def fmt_num(x):
        if isinstance(x, float) and x == int(x): return f"{int(x)}.0"
        return str(float(x)) if isinstance(x, (int, float)) else str(x)
    def fmt_matrix(rows, cols, data, indent=4):
        lines = []
        for r in range(rows):
            row_data = [fmt_num(data[r * cols + c]) for c in range(cols)]
            lines.append(f"{' ' * indent}{', '.join(row_data)}")
        return ",\n".join(lines)
    lines = ["%YAML:1.0"]
    lines.append(f"image_width: {calib.get('image_width', 640)}")
    lines.append(f"image_height: {calib.get('image_height', 480)}")
    lines.append(f"camera_name: {calib.get('camera_name', 'usb_cam')}")
    lines.append("")
    cm = calib.get("camera_matrix", {})
    lines.append("camera_matrix:")
    lines.append(f"  rows: {cm.get('rows', 3)}"); lines.append(f"  cols: {cm.get('cols', 3)}")
    lines.append(f"  data: [{fmt_matrix(3, 3, cm.get('data', [600.0,0,320.0,0,600.0,240.0,0,0,1.0]))}]")
    lines.append(""); lines.append(f"distortion_model: {calib.get('distortion_model', 'plumb_bob')}")
    lines.append("")
    dc = calib.get("distortion_coefficients", {})
    lines.append("distortion_coefficients:"); lines.append(f"  rows: {dc.get('rows', 1)}")
    lines.append(f"  cols: {dc.get('cols', 5)}"); lines.append(f"  data: [{fmt_matrix(1, 5, dc.get('data', [0.0]*5))}]")
    lines.append("")
    rm = calib.get("rectification_matrix", {})
    lines.append("rectification_matrix:"); lines.append(f"  rows: {rm.get('rows', 3)}")
    lines.append(f"  cols: {rm.get('cols', 3)}")
    lines.append(f"  data: [{fmt_matrix(3, 3, rm.get('data', [1.0,0,0,0,1.0,0,0,0,1.0]))}]")
    lines.append("")
    pm = calib.get("projection_matrix", {})
    lines.append("projection_matrix:"); lines.append(f"  rows: {pm.get('rows', 3)}")
    lines.append(f"  cols: {pm.get('cols', 4)}")
    lines.append(f"  data: [{fmt_matrix(3, 4, pm.get('data', [600.0,0,320.0,0,0,600.0,240.0,0,0,0,1.0,0]))}]")
    lines.append("")
    return "\n".join(lines)


def _json_calib_to_yaml_stereo(calib: dict) -> str:
    """Convert stereo calibration JSON to ORB_SLAM3 stereo YAML format.
    Includes left/right intrinsics, Tlr (left→right transform),
    Camera.bf (baseline×fx), ThDepth, and rectification matrices.
    """
    def _flat(lst, default):
        """Extract flat list from either list or {rows,cols,data} dict."""
        if isinstance(lst, dict):
            return lst.get('data', default)
        if isinstance(lst, list) and len(lst) > 0:
            return lst
        return default

    def fmt_num(x):
        if isinstance(x, float) and x == int(x):
            return f"{int(x)}.0"
        return str(float(x)) if isinstance(x, (int, float)) else str(x)

    def fmt_matrix(rows, cols, data, indent=4):
        lines = []
        for r in range(rows):
            row_data = [fmt_num(data[r * cols + c]) for c in range(cols)]
            lines.append(f"{' ' * indent}{', '.join(row_data)}")
        return ",\n".join(lines)

    # Extract data
    cm_left = _flat(calib.get("camera_matrix", []), [600.0, 0, 320, 0, 600.0, 240, 0, 0, 1.0])
    dc_left = _flat(calib.get("distortion_coefficients", []), [0.0]*5)
    cm_right = _flat(calib.get("camera2_matrix", []), [600.0, 0, 320, 0, 600.0, 240, 0, 0, 1.0])
    dc_right = _flat(calib.get("camera2_distortion", []), [0.0]*5)
    R = _flat(calib.get("R", []), [1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0])
    T = _flat(calib.get("T", []), [0.0, 0.0, 0.0])

    w = calib.get("image_width", 640)
    h = calib.get("image_height", 480)
    fx = cm_left[0] if len(cm_left) > 0 else 600.0
    baseline = calib.get("baseline_m", abs(T[0]) if len(T) > 0 else 0.05)
    camera_bf = calib.get("camera_bf", fx * baseline)
    th_depth = calib.get("th_depth", 40.0)

    lines = ["%YAML:1.0"]
    lines.append("")
    lines.append("#--------------------------------------------------------------------------------------------")
    lines.append("# Camera Parameters. Adjust them!")
    lines.append("#--------------------------------------------------------------------------------------------")
    lines.append("Camera.type: \"PinHole\"")
    lines.append("")

    # Left camera
    lines.append("# Left Camera calibration and distortion parameters (OpenCV)")
    lines.append(f"Camera.fx: {fmt_num(cm_left[0] if len(cm_left) > 0 else 600)}")
    lines.append(f"Camera.fy: {fmt_num(cm_left[4] if len(cm_left) > 4 else 600)}")
    lines.append(f"Camera.cx: {fmt_num(cm_left[2] if len(cm_left) > 2 else 320)}")
    lines.append(f"Camera.cy: {fmt_num(cm_left[5] if len(cm_left) > 5 else 240)}")
    lines.append("")
    lines.append("# distortion parameters")
    lines.append(f"Camera.k1: {fmt_num(dc_left[0] if len(dc_left) > 0 else 0)}")
    lines.append(f"Camera.k2: {fmt_num(dc_left[1] if len(dc_left) > 1 else 0)}")
    lines.append(f"Camera.p1: {fmt_num(dc_left[2] if len(dc_left) > 2 else 0)}")
    lines.append(f"Camera.p2: {fmt_num(dc_left[3] if len(dc_left) > 3 else 0)}")
    lines.append("")

    # Right camera
    lines.append("# Right Camera calibration and distortion parameters (OpenCV)")
    lines.append(f"Camera2.fx: {fmt_num(cm_right[0] if len(cm_right) > 0 else 600)}")
    lines.append(f"Camera2.fy: {fmt_num(cm_right[4] if len(cm_right) > 4 else 600)}")
    lines.append(f"Camera2.cx: {fmt_num(cm_right[2] if len(cm_right) > 2 else 320)}")
    lines.append(f"Camera2.cy: {fmt_num(cm_right[5] if len(cm_right) > 5 else 240)}")
    lines.append("")
    lines.append("# distortion parameters")
    lines.append(f"Camera2.k1: {fmt_num(dc_right[0] if len(dc_right) > 0 else 0)}")
    lines.append(f"Camera2.k2: {fmt_num(dc_right[1] if len(dc_right) > 1 else 0)}")
    lines.append(f"Camera2.p1: {fmt_num(dc_right[2] if len(dc_right) > 2 else 0)}")
    lines.append(f"Camera2.p2: {fmt_num(dc_right[3] if len(dc_right) > 3 else 0)}")
    lines.append("")

    # Tlr: 3x4 or 4x4 transformation matrix from left to right camera
    # Build Tlr as [R|T] 3x4 matrix with optional 4th row [0,0,0,1]
    has_R = len(R) >= 9
    has_T = len(T) >= 3
    if has_R and has_T:
        tlr_data = [
            R[0], R[1], R[2], T[0],
            R[3], R[4], R[5], T[1],
            R[6], R[7], R[8], T[2]
        ]
    else:
        tlr_data = [1.0, 0, 0, baseline, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]

    lines.append("Tlr: !!opencv-matrix")
    lines.append("  rows: 3")
    lines.append("  cols: 4")
    lines.append("  dt: f")
    lines.append(f"  data: [{fmt_matrix(3, 4, tlr_data)}]")
    lines.append("")

    lines.append("")
    lines.append("# Camera resolution")
    lines.append(f"Camera.width: {w}")
    lines.append(f"Camera.height: {h}")
    lines.append("")
    lines.append("# Camera frames per second")
    lines.append("Camera.fps: 30.0")
    lines.append("")
    lines.append("# Color order of the images (0: BGR, 1: RGB. It is ignored if images are grayscale)")
    lines.append("Camera.RGB: 1")
    lines.append("")
    lines.append("# Image scale, it changes the image size to be processed (<1.0: reduce, >1.0: increase)")
    lines.append("Camera.imageScale: 1.0")
    lines.append("")
    lines.append("# Close/Far threshold. Baseline times.")
    lines.append(f"ThDepth: {fmt_num(th_depth)}")
    lines.append("# stereo baseline times fx")
    lines.append(f"Camera.bf: {fmt_num(camera_bf)}")
    lines.append("")

    return "\n".join(lines)


CAMERA_CALIB_STEREO_FILE = Path("/opt/spotbot/config/orb_slam3_stereo.yaml")

def _is_default_camera_calib(calib: dict) -> bool:
    cm = calib.get("camera_matrix", {})
    if isinstance(cm, list):
        fx = cm[0] if len(cm) > 0 else 600.0
        fy = cm[4] if len(cm) > 4 else 600.0
    elif isinstance(cm, dict):
        data = cm.get("data", [600.0, 0.0, 320.0, 0.0, 600.0, 240.0, 0.0, 0.0, 1.0])
        fx = data[0] if len(data) > 0 else 600.0
        fy = data[4] if len(data) > 4 else 600.0
    else:
        return True
    if abs(fx - 600.0) < 0.1 and abs(fy - 600.0) < 0.1:
        dc = calib.get("distortion_coefficients", {})
        ddata = dc if isinstance(dc, list) else dc.get("data", [0.0, 0.0, 0.0, 0.0, 0.0]) if isinstance(dc, dict) else [0.0] * 5
        if all(abs(d) < 0.001 for d in ddata[:5]):
            return True
    return False

def fetch_camera_cals_from_gateway():
    time.sleep(7)
    for cam_id in [1, 2]:
        try:
            url = f"{GATEWAY_URL}/core/camera/calibration/{cam_id}"
            req = urllib.request.Request(url, headers={"X-API-Token": API_TOKEN}, method="GET")
            with urllib.request.urlopen(req, context=ssl_ctx, timeout=10) as resp:
                calib = json.loads(resp.read().decode("utf-8"))
            yaml_data = _json_calib_to_yaml(calib)
            paths = [CAMERA_CALIB_MONO_FILE, CAMERA_CALIB_LEFT_FILE] if cam_id == 1 else [CAMERA_CALIB_RIGHT_FILE]
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(yaml_data)
            is_default = _is_default_camera_calib(calib)
            mapping = get_camera_devices()
            fp = mapping.get(cam_id, {}).get("fingerprint") if isinstance(mapping.get(cam_id), dict) else None
            save_calibration_status(cam_id, not is_default, fp)
        except Exception as e:
            print(f"[Agent] Impossible de recuperer calibration camera {cam_id}: {e}")

    try:
        url = f"{GATEWAY_URL}/core/camera/calibration/stereo"
        req = urllib.request.Request(url, headers={"X-API-Token": API_TOKEN}, method="GET")
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=10) as resp:
            stereo_calib = json.loads(resp.read().decode("utf-8"))
        if stereo_calib.get("is_calibrated"):
            yml = _json_calib_to_yaml_stereo(stereo_calib)
            CAMERA_CALIB_STEREO_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CAMERA_CALIB_STEREO_FILE, "w", encoding="utf-8") as f:
                f.write(yml)
            print(f"[Agent] Calibration stereo recuperee → {CAMERA_CALIB_STEREO_FILE}")
    except Exception as e:
        print(f"[Agent] Impossible de recuperer calibration stereo: {e}")
