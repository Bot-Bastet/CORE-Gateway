import subprocess
import os
import glob
import json
import urllib.request
import threading
from pathlib import Path

import config
from sys_control import is_spotbot_service_active

ARDUINO_VERSION_FILE = Path("/opt/spotbot/arduino_version.txt")

def is_arduino_connected() -> bool:
    """Vérifie si le microcontrôleur Arduino Mega est physiquement connecté."""
    try:
        import serial.tools.list_ports
        for p in serial.tools.list_ports.comports():
            desc = (p.description or '').lower()
            if 'arduino' in desc or (p.vid == 0x2341 and p.pid in (0x0010, 0x0042)):
                return True
    except Exception:
        pass
    for pattern in ['/dev/ttyUSB*', '/dev/ttyACM*']:
        if glob.glob(pattern):
            return True
    return False

def get_arduino_version() -> str:
    robot_ver = config.get_version()
    if ARDUINO_VERSION_FILE.exists():
        try:
            arduino_ver = ARDUINO_VERSION_FILE.read_text().strip()
            if robot_ver != arduino_ver:
                print(f"[Agent] Sync Arduino version: {arduino_ver} -> {robot_ver}")
                ARDUINO_VERSION_FILE.write_text(robot_ver)
                return robot_ver
            return arduino_ver
        except Exception:
            pass
    try:
        ARDUINO_VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        ARDUINO_VERSION_FILE.write_text(robot_ver)
    except Exception:
        pass
    return robot_ver

def report_arduino_progress(status: str, percent: int):
    try:
        url = f"{config.GATEWAY_URL}/system/update/arduino/progress"
        req = urllib.request.Request(
            url,
            data=json.dumps({"status": status, "percent": percent}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-API-Token": config.API_TOKEN
            },
            method="POST"
        )
        with urllib.request.urlopen(req, context=config.ssl_ctx, timeout=5) as resp:
            resp.read()
    except Exception as e:
        print(f"[Agent] Erreur envoi progrès Arduino : {e}")

def _ensure_arduino_cli() -> bool:
    try:
        r = subprocess.run(["arduino-cli", "version"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            print(f"[Agent] arduino-cli OK : {r.stdout.strip()}")
            return True
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[Agent] arduino-cli check error: {e}")

    print("[Agent] arduino-cli introuvable — installation...")
    try:
        install = subprocess.run(
            "curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh",
            shell=True, capture_output=True, text=True, timeout=120,
            env={**os.environ, "BINDIR": "/usr/local/bin"}
        )
        if install.returncode == 0:
            print("[Agent] arduino-cli installé.")
            return True
        print(f"[Agent] Echec install arduino-cli: {install.stderr}")
        return False
    except Exception as e:
        print(f"[Agent] Exception install arduino-cli: {e}")
        return False

def _ensure_arduino_core() -> bool:
    try:
        r = subprocess.run(
            ["arduino-cli", "core", "list"],
            capture_output=True, text=True, timeout=30
        )
        if "arduino:avr" in r.stdout:
            print("[Agent] Core arduino:avr déjà installé.")
            return True
    except Exception:
        pass

    print("[Agent] Installation du core arduino:avr...")
    try:
        r = subprocess.run(
            ["arduino-cli", "core", "update-index"],
            capture_output=True, text=True, timeout=60
        )
        r2 = subprocess.run(
            ["arduino-cli", "core", "install", "arduino:avr"],
            capture_output=True, text=True, timeout=300
        )
        if r2.returncode == 0:
            print("[Agent] Core arduino:avr installé.")
            return True
        print(f"[Agent] Echec install core: {r2.stderr}")
        return False
    except Exception as e:
        print(f"[Agent] Exception install core: {e}")
        return False

def _ensure_arduino_lib(lib_name: str) -> bool:
    try:
        r = subprocess.run(
            ["arduino-cli", "lib", "list"],
            capture_output=True, text=True, timeout=30
        )
        if lib_name.lower().replace(" ", "") in r.stdout.lower().replace(" ", ""):
            print(f"[Agent] Lib '{lib_name}' déjà installée.")
            return True
    except Exception:
        pass

    print(f"[Agent] Installation librairie '{lib_name}'...")
    try:
        r = subprocess.run(
            ["arduino-cli", "lib", "install", lib_name],
            capture_output=True, text=True, timeout=120
        )
        if r.returncode == 0:
            print(f"[Agent] Lib '{lib_name}' installée.")
            return True
        print(f"[Agent] Echec install lib: {r.stderr}")
        return False
    except Exception as e:
        print(f"[Agent] Exception install lib: {e}")
        return False

def flash_arduino_task():
    print("[Agent] ═══ Début flash Arduino ═══")
    report_arduino_progress("stopping_services", 5)

    was_active = is_spotbot_service_active()
    if was_active:
        print("[Agent] Arrêt de spotbot.service...")
        subprocess.run(["sudo", "systemctl", "stop", "spotbot.service"], timeout=15)

    try:
        report_arduino_progress("checking_tools", 10)
        if not _ensure_arduino_cli():
            report_arduino_progress("failed_no_cli", 0)
            return

        report_arduino_progress("installing_core", 15)
        if not _ensure_arduino_core():
            report_arduino_progress("failed_no_core", 0)
            return

        report_arduino_progress("installing_libs", 20)
        _ensure_arduino_lib("SparkFun BNO08x Cortex Based IMU")
        _ensure_arduino_lib("Servo")

        report_arduino_progress("detecting_device", 25)
        port = None
        try:
            import serial.tools.list_ports
            for p in serial.tools.list_ports.comports():
                desc = (p.description or '').lower()
                if 'arduino' in desc or (p.vid == 0x2341 and p.pid in (0x0010, 0x0042, 0x0043, 0x0044)):
                    port = p.device
                    print(f"[Agent] Arduino détecté via pyserial : {port}")
                    break
        except Exception as e:
            print(f"[Agent] pyserial error: {e}")

        if not port:
            for pattern in ['/dev/ttyACM*', '/dev/ttyUSB*']:
                ports = sorted(glob.glob(pattern))
                if ports:
                    port = ports[0]
                    print(f"[Agent] Arduino détecté via glob : {port}")
                    break

        if not port:
            print("[Agent] ✗ Aucun Arduino trouvé. Vérifiez le câble USB.")
            report_arduino_progress("failed_no_device", 0)
            return

        report_arduino_progress("preparing_sketch", 30)
        sketch_src  = Path(__file__).parent / "arduino" / "spotbot_controller"
        sketch_dest = Path("/opt/spotbot/arduino/spotbot_controller")
        build_path  = Path("/tmp/spotbot_arduino_build")

        sketch_dest.parent.mkdir(parents=True, exist_ok=True)
        build_path.mkdir(parents=True, exist_ok=True)

        if sketch_src.exists():
            if sketch_src.resolve() != sketch_dest.resolve():
                import shutil
                if sketch_dest.exists():
                    shutil.rmtree(sketch_dest)
                shutil.copytree(sketch_src, sketch_dest)
                print(f"[Agent] Sketch copié vers {sketch_dest}")
            else:
                print(f"[Agent] Le sketch est déjà à sa destination : {sketch_dest}")
        elif not sketch_dest.exists():
            print(f"[Agent] ✗ Sketch introuvable : {sketch_src} ni {sketch_dest}")
            report_arduino_progress("failed_no_sketch", 0)
            return
        else:
            print(f"[Agent] Utilisation du sketch existant sur {sketch_dest}")

        try:
            ino_file = sketch_dest / "spotbot_controller.ino"
            if ino_file.exists():
                version_str = config.get_version()
                content = ino_file.read_text(encoding='utf-8', errors='ignore')
                import re
                new_content = re.sub(
                    r'#define\s+SKETCH_VERSION\s+"[^"]*"',
                    f'#define SKETCH_VERSION    "{version_str}"',
                    content
                )
                if new_content != content:
                    ino_file.write_text(new_content, encoding='utf-8')
                    print(f"[Agent] Version {version_str} injectée dans {ino_file.name}")
        except Exception as e:
            print(f"[Agent] Erreur injection version sketch: {e}")

        report_arduino_progress("compiling", 45)
        print(f"[Agent] Compilation de {sketch_dest}...")
        comp_res = subprocess.run([
            "arduino-cli", "compile",
            "--fqbn", "arduino:avr:mega",
            "--build-path", str(build_path),
            str(sketch_dest)
        ], capture_output=True, text=True, timeout=300)

        if comp_res.returncode != 0:
            print(f"[Agent] ✗ Erreur compilation:\n{comp_res.stderr}")
            report_arduino_progress(f"failed_compilation: {comp_res.stderr[:200]}", 0)
            return
        print("[Agent] ✓ Compilation réussie.")

        report_arduino_progress("flashing", 75)
        print(f"[Agent] Upload sur {port}...")
        upload_res = subprocess.run([
            "arduino-cli", "upload",
            "--fqbn", "arduino:avr:mega",
            "--port", port,
            "--input-dir", str(build_path)
        ], capture_output=True, text=True, timeout=120)

        if upload_res.returncode != 0:
            print(f"[Agent] ✗ Erreur upload:\n{upload_res.stderr}")
            report_arduino_progress(f"failed_flash: {upload_res.stderr[:200]}", 0)
            return
        print("[Agent] ✓ Upload réussi.")

        version = config.get_version()
        try:
            ARDUINO_VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
            ARDUINO_VERSION_FILE.write_text(version)
        except Exception as e_write:
            print(f"[Agent] Erreur ecriture version Arduino: {e_write}")
        try:
            import tempfile
            tmp = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt')
            tmp.write(version)
            tmp.close()
            subprocess.run(['sudo', 'mv', tmp.name, str(ARDUINO_VERSION_FILE)], check=True, timeout=5)
            print(f"[Agent] Version Arduino ecrite via sudo: {version}")
        except Exception as e_sudo:
            print(f"[Agent] Echec ecriture version Arduino (meme via sudo): {e_sudo}")

        report_arduino_progress("idle", 100)
        print("[Agent] ═══ Flash Arduino terminé avec succès ! ═══")

    except subprocess.TimeoutExpired as e:
        print(f"[Agent] ✗ Timeout : {e}")
        report_arduino_progress("failed_timeout", 0)
    except Exception as e:
        print(f"[Agent] ✗ Erreur générale flash : {e}")
        report_arduino_progress("failed_error", 0)
    finally:
        if was_active:
            print("[Agent] Redémarrage de spotbot.service...")
            subprocess.run(["sudo", "systemctl", "start", "spotbot.service"], timeout=15)

def trigger_arduino_flash():
    threading.Thread(target=flash_arduino_task, daemon=True).start()
