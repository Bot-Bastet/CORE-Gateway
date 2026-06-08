import asyncio
import websockets
import cv2
import subprocess
import threading
import sys
import os

RTSP_URL = "rtsp://127.0.0.1:48554/cam1"
WS_URL = "ws://127.0.0.1:44888/ws/robot"

# La capture webcam a été déplacée directement dans CORE-Node pour la simulation locale.

async def robot_websocket():
    print("🤖 [Cerveau] Connexion au Hub Central...")
    while True:
        try:
            async with websockets.connect(WS_URL) as ws:
                print("✅ [Cerveau] Connecté au Gateway.")
                
                # Simuler l'envoi d'un message audio/texte au démarrage pour tester
                welcome_msg = '{"type": "chat", "text": "Bonjour, je suis Bastet, initialisé et prêt!"}'
                await ws.send(welcome_msg)
                
                import json
                while True:
                    msg = await ws.recv()
                    print(f"📩 [Cerveau] Reçu du Node/App : {msg}")
                    
                    try:
                        data = json.loads(msg)
                        if data.get("type") == "feature_request":
                            feature = data.get("feature")
                            state = data.get("state")
                            print(f"⚙️ [Cerveau] Demande d'activation de {feature} à {state}. Envoi de l'acquittement...")
                            
                            # Répondre avec un acquittement
                            ack = {
                                "type": "feature_ack",
                                "feature": feature,
                                "state": state,
                                "status": "ok"
                            }
                            await ws.send(json.dumps(ack))
                    except json.JSONDecodeError:
                        # Message texte ou audio brut
                        pass
        except Exception as e:
            print(f"⚠️ [Cerveau] Déconnecté du Gateway ({e}). Reconnexion dans 5s...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    print("🤖 --- Démarrage du Simulateur Robot ---")
    
    # Lancement de la boucle WebSocket dans le thread principal
    try:
        asyncio.run(robot_websocket())
    except KeyboardInterrupt:
        print("🤖 --- Arrêt ---")
        sys.exit(0)
