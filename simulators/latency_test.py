import asyncio
import subprocess
import os

RTSP_URL = "rtsp://ha.arthonetwork.fr:48554/robot/latency_test"

async def start_latency_test():
    camera_name = "ACER HD User Facing"
    print(f"--- TEST DE LATENCE MINIMALE ---")
    print(f"Cible : {RTSP_URL}")
    
    # Commande ultra-optimisée pour le temps réel
    # On ajoute des logs plus verbeux pour voir la vitesse de traitement
    command = [
        "ffmpeg",
        "-hide_banner",
        "-f", "dshow",
        "-i", f"video={camera_name}",
        "-vf", (
            "format=yuv420p,scale=640:-1,"
            "drawtext=text='%{localtime\\:%H\\\\\\:%M\\\\\\:%S}.%{eif\\:mod(t*1000,1000)\\:d\\:3}':"
            "fontcolor=yellow:fontsize=32:x=w-tw-10:y=h-th-10:box=1:boxcolor=black@0.5"
        ),
        "-vcodec", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-g", "30",                     # GOP court (1s)
        "-bf", "0",                     # Pas de B-frames pour éviter le délai de réordonnancement
        "-f", "rtsp",
        "-rtsp_transport", "tcp",       # TCP est plus fiable pour le test initial, on passera en UDP après
        RTSP_URL
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT
        )
        print("✅ Processus FFmpeg lancé.")
        
        # Lire les premières lignes pour vérifier le démarrage
        for i in range(20):
            line = await process.stdout.readline()
            if not line: break
            print(f"[FFmpeg] {line.decode().strip()}")
            
        print("--- Stream actif. Surveillez le dashboard (WebRTC) pour la latence visuelle. ---")
        
        # On laisse tourner un peu puis on coupe pour le test
        await asyncio.sleep(10)
        process.terminate()
        print("--- Fin du test technique ---")
        
    except Exception as e:
        print(f"Erreur : {e}")

if __name__ == "__main__":
    asyncio.run(start_latency_test())
