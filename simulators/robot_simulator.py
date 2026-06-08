import asyncio
import websockets
import cv2
import subprocess
import threading
import sys
import os

RTSP_URL = "rtsp://127.0.0.1:8554/cam1"
WS_URL = "ws://127.0.0.1:8001/ws/robot"

def stream_webcam():
    print("🎥 [Vision] Démarrage de la capture Webcam...")
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("❌ [Vision] Impossible d'ouvrir la webcam.")
        return

    fps = int(cap.get(cv2.CAP_PROP_FPS))
    if fps == 0: fps = 30
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    
    print(f"🎥 [Vision] Résolution: {width}x{height} @ {fps} FPS")

    # Commande FFmpeg pour lire les frames brutes depuis l'entrée standard et pousser vers RTSP
    command = [
        'ffmpeg',
        '-y',
        '-f', 'rawvideo',
        '-vcodec', 'rawvideo',
        '-pix_fmt', 'bgr24',
        '-s', f"{width}x{height}",
        '-r', str(fps),
        '-i', '-',
        '-c:v', 'libx264',
        '-preset', 'ultrafast',
        '-tune', 'zerolatency',
        '-pix_fmt', 'yuv420p',
        '-f', 'rtsp',
        '-rtsp_transport', 'tcp',
        RTSP_URL
    ]

    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"🎥 [Vision] Streaming vers {RTSP_URL} en cours...")
    except FileNotFoundError:
        print("❌ [Vision] FFmpeg n'est pas installé ou n'est pas dans le PATH. Le stream RTSP est désactivé.")
        return

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            try:
                process.stdin.write(frame.tobytes())
            except Exception as e:
                print("❌ [Vision] Erreur d'écriture FFmpeg:", e)
                break
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        process.stdin.close()
        process.wait()
        print("🎥 [Vision] Streaming arrêté.")

async def robot_websocket():
    print("🤖 [Cerveau] Connexion au Hub Central...")
    while True:
        try:
            async with websockets.connect(WS_URL) as ws:
                print("✅ [Cerveau] Connecté au Gateway.")
                
                # Simuler l'envoi d'un message audio/texte au démarrage pour tester
                welcome_msg = '{"type": "chat", "text": "Bonjour, je suis Bastet, initialisé et prêt!"}'
                await ws.send(welcome_msg)
                
                while True:
                    msg = await ws.recv()
                    print(f"📩 [Cerveau] Reçu du Node/App : {msg}")
                    # Ici le vrai robot transmettrait l'audio TTS au haut-parleur
                    
        except Exception as e:
            print(f"⚠️ [Cerveau] Déconnecté du Gateway ({e}). Reconnexion dans 5s...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    print("🤖 --- Démarrage du Simulateur Robot ---")
    
    # Lancement de la vidéo dans un thread séparé
    video_thread = threading.Thread(target=stream_webcam, daemon=True)
    video_thread.start()
    
    # Lancement de la boucle WebSocket dans le thread principal
    try:
        asyncio.run(robot_websocket())
    except KeyboardInterrupt:
        print("🤖 --- Arrêt ---")
        sys.exit(0)
