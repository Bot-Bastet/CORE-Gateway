import asyncio
import websockets
import json
import httpx
import sys
import os
import subprocess

# Configuration pour le serveur de production
PROD_TOKEN = "bst_c9f28d3a1e4b85c7f0d4b9a2e6f1c3d5"
BASE_URL = "https://ha.arthonetwork.fr:44888"
RTSP_URL = "rtsp://ha.arthonetwork.fr:48554/robot/cam1"
# WebSocket URL sécurisée (WSS) avec le token de production
WS_URL = f"wss://ha.arthonetwork.fr:44888/ws/robot?token={PROD_TOKEN}"

async def stream_video():
    """Lance le flux vidéo avec des paramètres d'optimisation extrême pour la latence."""
    print(f"[Vidéo] Démarrage du flux ULTRA-LOW-LATENCY vers {RTSP_URL}...")
    
    camera_name = "ACER HD User Facing"
    
    # Commande stabilisée pour la production
    command_camera = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-f", "dshow",
        "-rtbufsize", "100M",
        "-i", f"video={camera_name}",
        "-vf", "format=yuv420p,scale=640:360",
        "-vcodec", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-g", "30",
        "-bf", "0",
        "-f", "rtsp",
        "-rtsp_transport", "tcp",       # TCP obligatoire pour la stabilité sur internet
        RTSP_URL
    ]

    # Mire de test (Repli)
    command_fallback = [
        "ffmpeg",
        "-re",
        "-f", "lavfi",
        "-i", "testsrc=size=640x480:rate=30",
        "-vf", "format=yuv420p,scale=640:360",
        "-vcodec", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-g", "30",
        "-bf", "0",
        "-f", "rtsp",
        "-rtsp_transport", "tcp",
        RTSP_URL
    ]

    while True:
        try:
            print(f"[Vidéo] Essai avec la caméra : {camera_name}")
            process = await asyncio.create_subprocess_exec(
                *command_camera,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE
            )
            
            # On attend un peu pour voir si ça crash (ex: caméra déjà utilisée)
            await asyncio.sleep(2)
            if process.returncode is not None:
                stdout, stderr = await process.communicate()
                print(f"[Vidéo] Caméra réelle en échec (Code {process.returncode}).")
                if stderr:
                    print(f"[Vidéo] Erreur caméra : {stderr.decode()}")
                
                print(f"[Vidéo] Passage en mode Mire de test.")
                # Lancement du mode Fallback
                process = await asyncio.create_subprocess_exec(
                    *command_fallback,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE
                )
            
            print("[Vidéo] Stream en cours...")
            # On attend la fin du processus (crash ou arrêt volontaire)
            stdout, stderr = await process.communicate()
            if process.returncode != 0 and stderr:
                err_text = stderr.decode()
                if "Broken pipe" in err_text:
                    print("[Vidéo] Le serveur a réinitialisé la connexion (Broken pipe). Reconnexion automatique...")
                else:
                    print(f"[Vidéo] FFmpeg s'est arrêté avec une erreur (Code {process.returncode}) :")
                    print(err_text)
            
        except Exception as e:
            print(f"[Vidéo] Erreur globale streaming : {e}")
        
        print("[Vidéo] Redémarrage dans 5s...")
        await asyncio.sleep(5)

async def update_robot_state():
    """Envoie périodiquement l'état du robot au serveur de production."""
    print(f"[État] Connexion à {BASE_URL}/core/state...")
    while True:
        try:
            state = {
                "seen_person": "Utilisateur Distant",
                "seen_objects": ["caméra", "serveur", "cloud"],
                "robot_status": "online",
                "last_chat": [{"role": "assistant", "content": "Connecté au serveur de production avec flux vidéo."}]
            }
            async with httpx.AsyncClient(verify=True) as client:
                response = await client.post(
                    f"{BASE_URL}/core/state",
                    json=state,
                    headers={"X-API-Token": PROD_TOKEN},
                    timeout=10.0
                )
                if response.status_code == 200:
                    print("[État] Mise à jour réussie sur la production.")
                else:
                    print(f"[État] Erreur {response.status_code}: {response.text}")
        except Exception as e:
            print(f"[État] Erreur de connexion production : {e}")
        
        await asyncio.sleep(10)

async def robot_websocket():
    """Gère la communication WSS temps-réel avec le serveur de production."""
    print(f"[Cerveau] Connexion WSS : {WS_URL}")
    while True:
        try:
            async with websockets.connect(WS_URL) as ws:
                print("[Cerveau] Connecté au Hub Central (PROD).")
                
                welcome_msg = {
                    "type": "chat",
                    "text": "Bastet Robot (PROD SIM + VIDEO) est en ligne."
                }
                await ws.send(json.dumps(welcome_msg))
                
                while True:
                    msg = await ws.recv()
                    print(f"[Cerveau] Message reçu de la PROD : {msg}")
                    
                    try:
                        data = json.loads(msg)
                        if data.get("type") == "feature_request":
                            print(f"[Cerveau] Commande PROD : {data.get('feature')} -> {data.get('state')}")
                            ack = {
                                "type": "feature_ack",
                                "feature": data.get("feature"),
                                "state": data.get("state"),
                                "status": "ok"
                            }
                            await ws.send(json.dumps(ack))
                    except json.JSONDecodeError:
                        pass
        except Exception as e:
            print(f"[Cerveau] Déconnecté de la PROD ({e}). Reconnexion dans 5s...")
            await asyncio.sleep(5)

async def main():
    # Exécution parallèle des 3 tâches : Vidéo, WebSocket et État
    await asyncio.gather(
        robot_websocket(),
        update_robot_state(),
        stream_video()
    )

if __name__ == "__main__":
    print("--- Démarrage du Simulateur Robot Complet (PRODUCTION) ---")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n--- Arrêt du simulateur ---")
        sys.exit(0)
