#!/usr/bin/env python3
"""
Real-time Agent for Bastet robot (Pi 5) - Modularized version.
"""
import os
import sys
import json
import time
import urllib.request
import subprocess
import threading
import re
from pathlib import Path

# Import our modules
import config
import metrics
from sys_control import stop_spotbot_service, start_spotbot_service, get_cap_device, start_ros2_listener, is_spotbot_service_active
import calib_worker
import arduino_flasher

from camera import (
    get_camera_devices,
    get_camera_fingerprint,
    get_calibration_status,
    save_calibration_status,
    detect_camera_change,
    get_active_video_devices,
    fetch_camera_cals_from_gateway,
    _list_physical_usb_video_devices
)
from wifi import get_wifi_list, connect_to_wifi, forget_wifi_network

def trigger_updater(version=None):
    print(f"[Agent] Lancement de la mise à jour... (version: {version or 'latest'})")
    try:
        cmd = ["sudo", "python3", "/opt/spotbot/updater.py"]
        if version:
            cmd.append(version)
        subprocess.Popen(cmd)
    except Exception as e:
        print(f"[Agent] Erreur lancement updater : {e}")

def check_camera_connected(cam_id: int) -> bool:
    """Vérifie si une caméra V4L2 est connectée au système."""
    mapping = get_camera_devices()
    dev = mapping.get(cam_id)
    if dev and isinstance(dev, dict):
        dev = dev.get("device", dev)
    if not dev:
        return False
    
    physical_devs = _list_physical_usb_video_devices()
    if physical_devs:
        try:
            real_dev = os.path.realpath(dev)
        except Exception:
            real_dev = dev
        return real_dev in physical_devs
        
    if dev and os.path.exists(dev):
        if cam_id == 2 and (dev == "/dev/video1" or dev == "/dev/video3"):
            return False
        return True
    return cam_id <= len(physical_devs)

# Arduino connection debouncer state
arduino_stable_state = True
arduino_miss_count = 0
arduino_hit_count = 0
ARDUINO_DEBOUNCE_THRESHOLD = 3

# Hibernation grace period
hibernation_deadline = 0
HIBERNATION_GRACE_SEC = 30

def get_arduino_stable_state() -> bool:
    global arduino_stable_state, arduino_miss_count, arduino_hit_count
    raw = arduino_flasher.is_arduino_connected()
    if raw:
        arduino_miss_count = 0
        arduino_hit_count += 1
        if arduino_hit_count >= ARDUINO_DEBOUNCE_THRESHOLD:
            arduino_stable_state = True
            arduino_hit_count = ARDUINO_DEBOUNCE_THRESHOLD
    else:
        arduino_hit_count = 0
        arduino_miss_count += 1
        if arduino_miss_count >= ARDUINO_DEBOUNCE_THRESHOLD:
            arduino_stable_state = False
            arduino_miss_count = ARDUINO_DEBOUNCE_THRESHOLD
    return arduino_stable_state

def update_status_loop():
    global hibernation_deadline
    print("[Agent] Démarrage du rapport d'état périodique...")
    while True:
        try:
            active = is_spotbot_service_active()
            now = time.time()
            if active:
                status = "online"
                hibernation_deadline = 0
            elif hibernation_deadline == 0:
                hibernation_deadline = now + HIBERNATION_GRACE_SEC
                status = "online"
            elif now < hibernation_deadline:
                status = "online"
            else:
                status = "hibernating"
            
            sys_metrics = metrics.get_system_metrics()
            cpu_percent = min(int(sys_metrics.get("cpu_load_1m", 0.0) * 25), 100)
            
            mapping = get_camera_devices()
            payload = {
                "seen_person": None,
                "seen_objects": [],
                "last_chat": [],
                "robot_status": status,
                "robot_version": config.get_version(),
                "arduino_version": arduino_flasher.get_arduino_version(),
                "camera_mapping": {
                    "left": mapping[1],
                    "right": mapping[2]
                },
                "sensors": {
                    "cpu_percent": cpu_percent,
                    "ram_percent": sys_metrics.get("ram_percent", 0.0),
                    "temp_c": sys_metrics.get("cpu_temp", 0.0),
                    "spotbot_service_active": active,
                    "system": sys_metrics,
                    "spotbot_service": "active" if active else "inactive",
                    "cam1_connected": check_camera_connected(1),
                    "cam2_connected": check_camera_connected(2),
                    "arduino_connected": get_arduino_stable_state(),
                    "calibration_status": get_calibration_status(),
                    "camera_changed": detect_camera_change(),
                    "available_video_devices": get_active_video_devices()
                },
                "ai_state": {
                    "tts": config.tts_target,
                    "stt": config.stt_target,
                    "chat": config.chat_target,
                    "yolo": config.yolo_state,
                    "face_rec": config.face_rec_state
                }
            }
            
            print(f"[Agent] POST status={status}, active={active}, arduino={payload['sensors']['arduino_connected']}")
            
            req = urllib.request.Request(
                f"{config.GATEWAY_URL}/core/state",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-API-Token": config.API_TOKEN
                },
                method="POST"
            )
            
            with urllib.request.urlopen(req, context=config.ssl_ctx, timeout=5) as resp:
                resp.read()
                print(f"[Agent] POST OK status={status}")
                
        except Exception as e:
            print(f"[Agent] Erreur envoi état : {e}")
            
        time.sleep(5)

def hourly_update_loop():
    print("[Agent] Démarrage de la surveillance des mises à jour (toutes les heures)...")
    while True:
        time.sleep(3600)
        try:
            if not is_spotbot_service_active():
                print("[Agent] Mode hibernation détecté. Vérification horaire de mise à jour...")
                trigger_updater()
        except Exception as e:
            print(f"[Agent] Erreur boucle mise à jour : {e}")

def fetch_offsets_from_gateway():
    time.sleep(5)
    try:
        url = f"{config.GATEWAY_URL}/core/calibration"
        req = urllib.request.Request(
            url,
            headers={"X-API-Token": config.API_TOKEN},
            method="GET"
        )
        with urllib.request.urlopen(req, context=config.ssl_ctx, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            offsets = data.get("offsets", [0.0] * 12)
            if len(offsets) != 12:
                offsets = [0.0] * 12
            limits = data.get("limits", [[0.0, 180.0]] * 12)
            inverts = data.get("inverts", [False] * 12)

        calib_file = Path("/opt/spotbot/config/calibration.json")
        calib_file.parent.mkdir(parents=True, exist_ok=True)
        with open(calib_file, "w", encoding="utf-8") as f:
            json.dump({"offsets": offsets, "limits": limits, "inverts": inverts}, f)
        print(f"[Agent] Configuration de calibration récupérée depuis la Gateway : offsets={offsets}, limits={limits}, inverts={inverts}")

        if config.ros2_process and config.ros2_process.stdin and config.ros2_process.stdin.writable():
            config.ros2_process.stdin.write(
                json.dumps({"type": "motor_calibration", "offsets": offsets, "limits": limits, "inverts": inverts}) + "\n"
            )
            config.ros2_process.stdin.flush()
            print("[Agent] Offsets et limites transmis au ros2_listener.")
    except Exception as e:
        print(f"[Agent] Impossible de récupérer les offsets depuis la Gateway : {e}")

def start_websocket_client():
    try:
        import websockets
        import asyncio
    except ImportError:
        print("[Agent] Module 'websockets' absent. Installation...")
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "websockets"], check=True)
            import websockets
            import asyncio
        except Exception as e:
            print(f"[Agent] Impossible d'installer 'websockets' : {e}")
            return

    async def ws_loop():
        uri = f"{config.WS_URL}?token={config.API_TOKEN}"
        while True:
            try:
                print(f"[Agent] Connexion WebSocket vers {config.WS_URL}...")
                async with websockets.connect(
                    uri,
                    ssl=config.ssl_ctx,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    print("[Agent] Connecté au WebSocket de la Gateway.")
                    await ws.send(json.dumps({"type": "chat", "text": f"Bastet Agent {config.get_version()} connecté."}))
                    
                    async def send_telemetry_loop():
                        ticks = 0
                        last_sent_json = None
                        cam1, cam2 = False, False
                        video_devices = []
                        while True:
                            if config.latest_telemetry:
                                try:
                                    if ticks % 15 == 0:
                                        try:
                                            cam1_res, cam2_res = await asyncio.gather(
                                                asyncio.to_thread(check_camera_connected, 1),
                                                asyncio.to_thread(check_camera_connected, 2),
                                                return_exceptions=True,
                                            )
                                            cam1 = cam1_res if not isinstance(cam1_res, BaseException) else False
                                            cam2 = cam2_res if not isinstance(cam2_res, BaseException) else False
                                            video_devices = get_active_video_devices()
                                        except Exception as probe_err:
                                            print(f"[Agent - WS Send] Probes cam échouées: {probe_err}")
                                            cam1, cam2 = False, False
                                            video_devices = []
                                    
                                    config.latest_telemetry.setdefault("sensors", {}).update({
                                        "cam1_connected": bool(cam1),
                                        "cam2_connected": bool(cam2),
                                        "available_video_devices": video_devices,
                                    })

                                    payload_str = json.dumps(config.latest_telemetry)
                                    if payload_str != last_sent_json:
                                        if ticks % 50 == 0:
                                            print(f"[Agent - WS Send] Envoi télémétrie périodique: {list(config.latest_telemetry.keys())}")
                                        await ws.send(payload_str)
                                        last_sent_json = payload_str
                                except Exception as e:
                                    print(f"[Agent - WS Send] Erreur lors de l'envoi WebSocket: {e}")
                                    break
                            ticks += 1
                            await asyncio.sleep(0.2)
                            
                    telemetry_task = asyncio.create_task(send_telemetry_loop())
                    
                    try:
                        while True:
                            msg = await ws.recv()
                            try:
                                data = json.loads(msg)
                                msg_type = data.get("type")
                                
                                if msg_type == "trigger_update":
                                    version = data.get("version")
                                    print(f"[Agent] Commande de mise à jour reçue ! (version: {version or 'latest'})")
                                    trigger_updater(version)
                                    
                                elif msg_type == "trigger_arduino_flash":
                                    print("[Agent] Commande de flash Arduino reçue !")
                                    arduino_flasher.trigger_arduino_flash()
                                    
                                elif msg_type == "start_robot":
                                    print("[Agent] Commande de démarrage du robot reçue !")
                                    subprocess.run(["sudo", "systemctl", "start", "spotbot.service"])
                                    
                                elif msg_type == "stop_robot":
                                    print("[Agent] Commande d'arrêt du robot reçue !")
                                    subprocess.run(["sudo", "systemctl", "stop", "spotbot.service"])
                                    
                                elif msg_type == "start_camera":
                                    cam = data.get("camera", 1)
                                    v_slam = data.get("v_slam", False)
                                    if not is_spotbot_service_active():
                                        print("[Agent] start_camera reçu mais spotbot.service est inactif ! Démarrage du service...")
                                        subprocess.run(["sudo", "systemctl", "start", "spotbot.service"])
                                        await asyncio.sleep(2.0)
                                    if v_slam:
                                        cal_status = get_calibration_status()
                                        cam_cal = cal_status.get(cam, {})
                                        if not cam_cal.get("calibrated", False):
                                            await ws.send(json.dumps({
                                                "type": "vslam_blocked",
                                                "camera": cam,
                                                "reason": "Calibration requise avant V-SLAM. Calibrez la caméra."
                                            }))
                                            print(f"[Agent] ⚠️ V-SLAM bloqué pour caméra {cam}: calibration invalide.")
                                            continue
                                    if config.ros2_process and config.ros2_process.stdin:
                                        config.ros2_process.stdin.write(json.dumps(data) + "\n")
                                        config.ros2_process.stdin.flush()
                                        print(f"[Agent] Start camera {cam} déléguée au ros2_listener.")
                                    
                                elif msg_type == "stop_camera":
                                    cam = data.get("camera", 1)
                                    if cam in config.calibration_cancel_events:
                                        config.calibration_cancel_events[cam].set()
                                    config.stereo_calibration_cancel_event.set()
                                    if config.ros2_process and config.ros2_process.stdin:
                                        config.ros2_process.stdin.write(json.dumps(data) + "\n")
                                        config.ros2_process.stdin.flush()
                                        print(f"[Agent] Stop camera {cam} déléguée au ros2_listener.")
                                        
                                elif msg_type == "query_camera_resolutions":
                                    cam = data.get("camera", 1)
                                    devices = get_camera_devices()
                                    dev = devices.get(cam)
                                    if dev and isinstance(dev, dict):
                                        dev = dev.get("device")
                                    resolutions = []
                                    if dev and os.path.exists(dev):
                                        try:
                                            result = subprocess.run(
                                                ["v4l2-ctl", "--list-formats-ext", "-d", dev],
                                                capture_output=True, text=True, timeout=5
                                            )
                                            if result.returncode == 0:
                                                seen = set()
                                                for line in result.stdout.split(chr(10)):
                                                    m = re.search(r'Size: Discrete (\d+)x(\d+)', line)
                                                    if m:
                                                        w, h = m.groups()
                                                        fmt = f'{w}x{h}'
                                                        if fmt not in seen:
                                                            seen.add(fmt)
                                                            resolutions.append(fmt)
                                        except Exception as e:
                                            print(f'[Agent] Erreur détection résolutions: {e}')
                                    if not resolutions:
                                        resolutions = ["640x480", "1280x720", "1920x1080"]
                                    await ws.send(json.dumps({
                                        "type": "camera_resolutions",
                                        "camera": cam,
                                        "resolutions": resolutions
                                    }))
                                    
                                elif msg_type == "motor_calibration":
                                    print("[Agent] Commande de calibration reçue !")
                                    try:
                                        offsets = data.get("offsets", [0.0] * 12)
                                        limits = data.get("limits", [[0.0, 180.0]] * 12)
                                        inverts = data.get("inverts", [False] * 12)
                                        calib_file = Path("/opt/spotbot/config/calibration.json")
                                        calib_file.parent.mkdir(parents=True, exist_ok=True)
                                        with open(calib_file, "w", encoding="utf-8") as f:
                                            json.dump({"offsets": offsets, "limits": limits, "inverts": inverts}, f)
                                        print("[Agent] Configuration de calibration sauvegardée localement.")
                                    except Exception as e:
                                        print(f"[Agent] Erreur écriture calibration locale : {e}")
                                    if config.ros2_process and config.ros2_process.stdin:
                                        config.ros2_process.stdin.write(json.dumps(data) + "\n")
                                        config.ros2_process.stdin.flush()
                                
                                elif msg_type == "arduino_cmd":
                                    print(f"[Agent] Commande Arduino reçue")
                                    if config.ros2_process and config.ros2_process.stdin:
                                        config.ros2_process.stdin.write(json.dumps(data) + "\n")
                                        config.ros2_process.stdin.flush()

                                elif msg_type == "cmd_vel":
                                    # Forward D-pad walking commands to ros2_listener → motion_node
                                    if config.ros2_process and config.ros2_process.stdin:
                                        config.ros2_process.stdin.write(json.dumps(data) + "\n")
                                        config.ros2_process.stdin.flush()

                                elif msg_type in ("nav_goal", "nav_path"):
                                    # Forward navigation goals/paths to ros2_listener
                                    if config.ros2_process and config.ros2_process.stdin:
                                        config.ros2_process.stdin.write(json.dumps(data) + "\n")
                                        config.ros2_process.stdin.flush()

                                elif msg_type == "robot_posture":
                                    # Forward posture/gait updates to ros2_listener
                                    if config.ros2_process and config.ros2_process.stdin:
                                        config.ros2_process.stdin.write(json.dumps(data) + "\n")
                                        config.ros2_process.stdin.flush()

                                elif msg_type == "manual_joint_control":
                                    print("[Agent] Commande contrôle articulations reçue")
                                    if config.ros2_process and config.ros2_process.stdin:
                                        config.ros2_process.stdin.write(json.dumps(data) + "\n")
                                        config.ros2_process.stdin.flush()
                                        
                                elif msg_type == "scan_wifi":
                                    print("[Agent] Commande de scan WiFi reçue !")
                                    networks = get_wifi_list()
                                    known_ssids = []
                                    known_passwords = {}
                                    conf_path = "/etc/wpa_supplicant/wpa_supplicant.conf"
                                    if os.path.exists(conf_path):
                                        try:
                                            with open(conf_path, "r") as f:
                                                content_wpa = f.read()
                                            for block in re.findall(r'network\s*=\s*\{([^}]*)\}', content_wpa, re.DOTALL):
                                                ssid_m = re.search(r'ssid\s*=\s*"([^"]+)"', block)
                                                psk_m  = re.search(r'psk\s*=\s*"([^"]+)"', block)
                                                if ssid_m:
                                                    s = ssid_m.group(1)
                                                    if s not in known_ssids:
                                                        known_ssids.append(s)
                                                    if psk_m:
                                                        known_passwords[s] = psk_m.group(1)
                                        except Exception as e_wpa:
                                            print(f"[Agent] Erreur lecture wpa_supplicant.conf : {e_wpa}")
                                    current_ssid = ""
                                    try:
                                        res_cur = subprocess.run(
                                            ["sudo", "iwgetid", "wlan0", "--raw"],
                                            capture_output=True, text=True, timeout=5
                                        )
                                        current_ssid = res_cur.stdout.strip()
                                    except Exception:
                                        pass
                                    await ws.send(json.dumps({
                                        "type": "wifi_list",
                                        "networks": networks,
                                        "known_ssids": known_ssids,
                                        "known_passwords": known_passwords,
                                        "current_ssid": current_ssid
                                    }))
                                    
                                elif msg_type == "connect_wifi":
                                    ssid = data.get("ssid")
                                    password = data.get("password")
                                    print(f"[Agent] Connexion WiFi vers {ssid}...")
                                    res = connect_to_wifi(ssid, password)
                                    await ws.send(json.dumps({"type": "wifi_connect_result", **res}))
                                    
                                elif msg_type == "forget_wifi":
                                    ssid = data.get("ssid")
                                    print(f"[Agent] Commande d'oubli WiFi pour {ssid}...")
                                    res = forget_wifi_network(ssid)
                                    await ws.send(json.dumps({"type": "wifi_forget_result", "ssid": ssid, **res}))
                                
                                elif msg_type == "save_camera_mapping":
                                    left = data.get("left")
                                    right = data.get("right")
                                    print(f"[Agent] Sauvegarde du mapping caméra: left={left}, right={right}")
                                    try:
                                        config.CAMERA_MAPPING_FILE.parent.mkdir(parents=True, exist_ok=True)
                                        with open(str(config.CAMERA_MAPPING_FILE), "w") as f_map:
                                            json.dump({"left": left, "right": right}, f_map)
                                        await ws.send(json.dumps({"type": "camera_mapping_saved", "status": "ok"}))
                                    except Exception as e_map:
                                        print(f"[Agent] Erreur sauvegarde mapping camera : {e_map}")
                                        await ws.send(json.dumps({"type": "camera_mapping_saved", "status": "error", "message": str(e_map)}))
                                
                                elif msg_type == "reset_calibration":
                                    print("[Agent] Commande de reinitialisation calibration recue !")
                                    try:
                                        if config.CALIB_STATUS_FILE.exists():
                                            config.CALIB_STATUS_FILE.unlink()
                                        for p in [config.CAMERA_CALIB_MONO_FILE, config.CAMERA_CALIB_LEFT_FILE, config.CAMERA_CALIB_RIGHT_FILE]:
                                            if p.exists():
                                                p.unlink()
                                        try:
                                            from camera import CAMERA_CALIB_STEREO_FILE
                                            if CAMERA_CALIB_STEREO_FILE.exists():
                                                CAMERA_CALIB_STEREO_FILE.unlink()
                                        except ImportError:
                                            pass
                                        await ws.send(json.dumps({"type": "calibration_reset", "status": "ok"}))
                                    except Exception as e_reset:
                                        print(f"[Agent] Erreur reset calibration: {e_reset}")
                                        await ws.send(json.dumps({"type": "calibration_reset", "status": "error", "message": str(e_reset)}))
                                
                                elif msg_type == "run_stereo_calib":
                                    print("[Agent] Commande de calibration stereo recue !")
                                    calib_worker.run_stereo_calib_thread(ws, asyncio.get_event_loop(), data)
                                    
                                elif msg_type == "run_mono_calib":
                                    print("[Agent] Commande de calibration mono recue !")
                                    calib_worker.run_mono_calib_thread(ws, asyncio.get_event_loop(), data)

                                elif msg_type == "feature_request":
                                    feature = data.get("feature")
                                    state = data.get("state")
                                    if feature == "audio":
                                        if state:
                                            config.tts_target = "node"
                                            config.stt_target = "node"
                                            config.chat_target = "node"
                                        else:
                                            config.tts_target = "robot"
                                            config.stt_target = "robot"
                                            config.chat_target = "robot"
                                    elif feature == "yolo":
                                        config.yolo_state = "node" if state else "robot"
                                    elif feature == "face_rec":
                                        config.face_rec_state = "node" if state else "robot"
                                        
                                    if config.latest_telemetry and "ai_state" in config.latest_telemetry:
                                        config.latest_telemetry["ai_state"] = {
                                            "tts": config.tts_target,
                                            "stt": config.stt_target,
                                            "chat": config.chat_target,
                                            "yolo": config.yolo_state,
                                            "face_rec": config.face_rec_state
                                        }
                                        
                                    ack_state = state
                                    if feature == "yolo":
                                        ack_state = (config.yolo_state == "node")
                                    elif feature == "face_rec":
                                        ack_state = (config.face_rec_state == "node")
                                    elif feature == "audio":
                                        ack_state = (config.tts_target == "node" or config.stt_target == "node" or config.chat_target == "node")

                                    await ws.send(json.dumps({
                                        "type": "feature_ack",
                                        "feature": feature,
                                        "state": ack_state,
                                        "status": "ok"
                                    }))

                                elif msg_type == "ai_control":
                                    feature = data.get("feature")
                                    target = data.get("target")
                                    if feature == "tts":
                                        config.tts_target = target
                                    elif feature == "stt":
                                        config.stt_target = target
                                    elif feature == "chat":
                                        config.chat_target = target
                                    elif feature == "yolo":
                                        config.yolo_state = target
                                    elif feature == "face_rec":
                                        config.face_rec_state = target
                                        
                                    if config.latest_telemetry and "ai_state" in config.latest_telemetry:
                                        config.latest_telemetry["ai_state"] = {
                                            "tts": config.tts_target,
                                            "stt": config.stt_target,
                                            "chat": config.chat_target,
                                            "yolo": config.yolo_state,
                                            "face_rec": config.face_rec_state
                                        }
                                        
                                    if feature in ("tts", "stt", "chat"):
                                        audio_active = (config.tts_target == "node" or config.stt_target == "node" or config.chat_target == "node")
                                        await ws.send(json.dumps({
                                            "type": "feature_ack",
                                            "feature": "audio",
                                            "state": audio_active,
                                            "status": "ok"
                                        }))
                                    elif feature == "yolo":
                                        await ws.send(json.dumps({
                                            "type": "feature_ack",
                                            "feature": "yolo",
                                            "state": (config.yolo_state == "node"),
                                            "status": "ok"
                                        }))
                                    elif feature == "face_rec":
                                        await ws.send(json.dumps({
                                            "type": "feature_ack",
                                            "feature": "face_rec",
                                            "state": (config.face_rec_state == "node"),
                                            "status": "ok"
                                        }))
                            except json.JSONDecodeError:
                                pass
                    finally:
                        for cam in config.calibration_cancel_events:
                            config.calibration_cancel_events[cam].set()
                        config.stereo_calibration_cancel_event.set()
                        telemetry_task.cancel()
            except Exception as e:
                print(f"[Agent] Déconnexion WebSocket ({e}). Reconnexion dans 5s...")
                await asyncio.sleep(5)

    asyncio.run(ws_loop())

if __name__ == "__main__":
    print(f"--- Démarrage de l'Agent Bastet ({config.get_version()}) ---")
    if not is_spotbot_service_active():
        print("[Agent] spotbot.service n'est pas actif au démarrage. Lancement du service...")
        start_spotbot_service()
    start_ros2_listener()
    
    threading.Thread(target=fetch_offsets_from_gateway, daemon=True).start()
    threading.Thread(target=fetch_camera_cals_from_gateway, daemon=True).start()
    
    t_status = threading.Thread(target=update_status_loop, daemon=True)
    t_status.start()
    
    t_update = threading.Thread(target=hourly_update_loop, daemon=True)
    t_update.start()
    
    start_websocket_client()
