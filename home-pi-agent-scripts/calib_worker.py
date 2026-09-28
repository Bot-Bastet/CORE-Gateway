import time
import json
import urllib.request
import asyncio
import cv2
import numpy as np

import config
from sys_control import get_cap_device, stop_spotbot_service, start_spotbot_service
from camera import get_camera_devices, save_calibration_status

def run_mono_calib_thread(ws_ref, loop, data):
    """Lance la tâche de calibration mono dans un thread dédié."""
    cam_id = data.get("camera", 1)
    config.calibration_cancel_events[cam_id].clear()
    cols = data.get("chessboard_cols", 9)
    rows = data.get("chessboard_rows", 6)
    square_mm = data.get("square_size_mm", 25)
    timeout_s = data.get("timeout_seconds", 300)

    def _send_mono_progress(pct, status):
        try:
            asyncio.run_coroutine_threadsafe(
                ws_ref.send(json.dumps({
                    "type": "mono_calib_progress",
                    "camera": cam_id,
                    "progress": pct,
                    "status": status
                })), loop
            )
        except Exception:
            pass

    def _mono_calib_task():
        cap = None
        try:
            _send_mono_progress(2, "Arret temporaire du service ROS 2...")
            stop_spotbot_service()
            time.sleep(2.0)
            
            mapping = get_camera_devices()
            dev_info = mapping.get(cam_id, {})
            dev = dev_info.get("device", f"/dev/video{2*(cam_id-1)}") if isinstance(dev_info, dict) else dev_info
            
            if config.calibration_cancel_events[cam_id].is_set():
                return
                
            _send_mono_progress(5, f"Ouverture camera {dev}...")
            cap = cv2.VideoCapture(get_cap_device(dev))
            if not cap.isOpened():
                asyncio.run_coroutine_threadsafe(
                    ws_ref.send(json.dumps({
                        "type": "mono_calib_result", "camera": cam_id,
                        "success": False, "message": f"Camera {dev} introuvable"
                    })), loop
                )
                return
            
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            
            pat = (cols, rows)
            sq_m = square_mm / 1000.0
            objp = np.zeros((cols * rows, 3), np.float32)
            objp[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2) * sq_m
            objpts, imgpts = [], []
            
            STABILITY_FRAMES = 8
            STABILITY_PX = 3.0
            DIVERSITY_CENTROID_PX = 30.0
            MAX_FRAMES = 2000
            MIN_FRAMES = 10
            
            stable_count = 0
            prev_centroid = None
            collected_centroids = []
            cap_n = 0
            warm = 0
            start_time = time.time()
            found_any = False
            
            _send_mono_progress(8, "Recherche du damier... Presentez le damier face a la camera.")
            
            for attempt in range(MAX_FRAMES):
                if time.time() - start_time > timeout_s:
                    # released in finally
                    asyncio.run_coroutine_threadsafe(
                        ws_ref.send(json.dumps({
                            "type": "mono_calib_result", "camera": cam_id,
                            "success": False, "message": f"Timeout ({timeout_s}s) - damier non detecte"
                        })), loop
                    )
                    return
                
                if config.calibration_cancel_events[cam_id].is_set():
                    try:
                        asyncio.run_coroutine_threadsafe(
                            ws_ref.send(json.dumps({
                                "type": "mono_calib_result", "camera": cam_id,
                                "success": False, "message": "Calibration annulee."
                            })), loop
                        )
                    except Exception:
                        pass
                    return
                    
                ret, frame = cap.read()
                if not ret:
                    continue
                warm += 1
                if warm < 5:
                    continue
                
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                mean_brightness = float(np.mean(gray))
                flags_cb = cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE + cv2.CALIB_CB_FAST_CHECK
                found, corners = cv2.findChessboardCorners(gray, pat, flags_cb)
                
                # Stream frame preview to client via Base64 JPEG
                try:
                    import base64
                    frame_to_send = frame.copy()
                    if found:
                        cv2.drawChessboardCorners(frame_to_send, pat, corners, found)
                    _, jpeg_buf = cv2.imencode('.jpg', frame_to_send, [cv2.IMWRITE_JPEG_QUALITY, 50])
                    jpeg_b64 = base64.b64encode(jpeg_buf).decode('utf-8')
                    asyncio.run_coroutine_threadsafe(
                        ws_ref.send(json.dumps({
                            "type": "mono_calib_frame",
                            "camera": cam_id,
                            "image": jpeg_b64
                        })), loop
                    )
                except Exception as e_send_frame:
                    print(f"Error sending calibration frame: {e_send_frame}")

                if found:
                    found_any = True
                    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
                    cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), crit)
                    
                    centroid = np.mean(corners.reshape(-1, 2), axis=0)
                    
                    if prev_centroid is not None:
                        movement = float(np.linalg.norm(centroid - prev_centroid))
                    else:
                        movement = 999.0
                    
                    prev_centroid = centroid
                    
                    if movement < STABILITY_PX:
                        stable_count += 1
                    else:
                        stable_count = 0
                    
                    if stable_count >= STABILITY_FRAMES:
                        is_diverse = True
                        for past_c in collected_centroids:
                            dist = float(np.linalg.norm(centroid - past_c))
                            if dist < DIVERSITY_CENTROID_PX:
                                is_diverse = False
                                break
                        
                        if is_diverse or cap_n == 0:
                            objpts.append(objp)
                            imgpts.append(corners)
                            collected_centroids.append(centroid)
                            cap_n += 1
                            stable_count = 0
                            
                            pct = 8 + int(52 * cap_n / max(MIN_FRAMES, 30))
                            status = "Stable" if stable_count < STABILITY_FRAMES else "✓ Capture"
                            _send_mono_progress(min(pct, 60), f"Damier detecte {status} — {cap_n} frames collectees...")
                            
                            if cap_n >= MIN_FRAMES:
                                break
                    
                    if attempt % 30 == 0:
                        st = "✓ Stable" if stable_count >= STABILITY_FRAMES else (f"Stabilisation... {stable_count}/{STABILITY_FRAMES}")
                        _send_mono_progress(8 + min(stable_count * 2, 20), f"Damier detecte — {st}")
                else:
                    stable_count = 0
                    prev_centroid = None
                    
                    if attempt % 60 == 0 and not found_any:
                        if mean_brightness < 15.0:
                            _send_mono_progress(8, "Recherche du damier (⚠️ Image tres sombre)... Allumez la lumiere.")
                        else:
                            _send_mono_progress(8, "Recherche du damier... Placez le damier face a la camera.")
            
            # released in finally
            
            if cap_n < 5:
                asyncio.run_coroutine_threadsafe(
                    ws_ref.send(json.dumps({
                        "type": "mono_calib_result", "camera": cam_id,
                        "success": False, "message": f"Pas assez de frames ({cap_n})"
                    })), loop
                )
                return
            
            _send_mono_progress(65, f"Calibration avec {cap_n} frames...")
            gray_shape = gray.shape[::-1]
            ret_cal, mtx, dist, rvecs, tvecs = cv2.calibrateCamera(objpts, imgpts, gray_shape, None, None)
            
            _send_mono_progress(80, "Sauvegarde des resultats...")
            
            calib_data = {
                "image_width": gray_shape[0], "image_height": gray_shape[1],
                "camera_name": f"usb_cam_{cam_id}",
                "camera_matrix": {
                    "rows": 3, "cols": 3,
                    "data": mtx.flatten().tolist()
                },
                "distortion_model": "plumb_bob",
                "distortion_coefficients": {
                    "rows": 1, "cols": len(dist.flatten()),
                    "data": dist.flatten().tolist()
                },
                "rectification_matrix": {
                    "rows": 3, "cols": 3,
                    "data": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
                },
                "projection_matrix": {
                    "rows": 3, "cols": 4,
                    "data": mtx[0].tolist() + [0.0] + mtx[1].tolist() + [0.0] + mtx[2].tolist() + [0.0]
                },
                "is_calibrated": True,
                "reprojection_error": float(ret_cal),
                "num_sample_frames": cap_n,
                "fx": float(mtx[0, 0]), "fy": float(mtx[1, 1]),
                "cx": float(mtx[0, 2]), "cy": float(mtx[1, 2]),
                "calibrated_at": time.strftime("%d/%m/%Y %H:%M:%S")
            }
            
            try:
                url = f"{config.GATEWAY_URL}/core/camera/calibration/{cam_id}"
                req = urllib.request.Request(url,
                    data=json.dumps(calib_data).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-API-Token": config.API_TOKEN},
                    method="POST")
                with urllib.request.urlopen(req, context=config.ssl_ctx, timeout=10) as resp:
                    resp.read()
                print(f"[Agent] Calibration mono cam {cam_id} sauvegardee sur Gateway.")
            except Exception as e_post:
                print(f"[Agent] Erreur sauvegarde mono Gateway: {e_post}")
            
            mapping = get_camera_devices()
            fp = mapping.get(cam_id, {}).get("fingerprint") if isinstance(mapping.get(cam_id), dict) else None
            save_calibration_status(cam_id, True, fp)
            
            _send_mono_progress(100, f"Calibration reussie ! (reproj: {ret_cal:.4f}, fx: {mtx[0,0]:.1f})")
            asyncio.run_coroutine_threadsafe(
                ws_ref.send(json.dumps({
                    "type": "mono_calib_result", "camera": cam_id,
                    "success": True,
                    "message": f"OK (reproj: {ret_cal:.4f}, fx: {mtx[0,0]:.1f})",
                    "reprojection_error": float(ret_cal),
                    "fx": round(float(mtx[0, 0]), 2),
                    "num_frames": cap_n
                })), loop
            )
        except Exception as e:
            import traceback; traceback.print_exc()
            try:
                asyncio.run_coroutine_threadsafe(
                    ws_ref.send(json.dumps({
                        "type": "mono_calib_result", "camera": cam_id,
                        "success": False, "message": str(e)
                    })), loop
                )
            except Exception:
                pass
        finally:
            if cap is not None:
                try:
                    cap.release()
                    print("[Agent] VideoCapture mono relache.")
                except Exception:
                    pass
            start_spotbot_service()

    import threading
    threading.Thread(target=_mono_calib_task, daemon=True).start()


def run_stereo_calib_thread(ws_ref, loop, data):
    """Lance la tâche de calibration stéréo dans un thread dédié."""
    config.stereo_calibration_cancel_event.clear()
    cols = data.get("chessboard_cols", 9)
    rows = data.get("chessboard_rows", 6)
    square_mm = data.get("square_size_mm", 25)
    num_pairs = data.get("num_pairs", 30)

    def _progress_sync(pct, status):
        try:
            asyncio.run_coroutine_threadsafe(
                ws_ref.send(json.dumps({
                    "type": "stereo_calib_progress",
                    "progress": pct,
                    "status": status
                })), loop
            )
        except Exception:
            pass

    def _stereo_calib_task():
        cap_l = None
        cap_r = None
        try:
            _progress_sync(2, "Arret temporaire du service ROS 2...")
            stop_spotbot_service()
            time.sleep(2.0)

            if config.stereo_calibration_cancel_event.is_set():
                return
            _progress_sync(5, "Ouverture des cameras...")
            mapping = get_camera_devices()
            left_dev = mapping.get(1, {}).get("device", "/dev/video0") if isinstance(mapping.get(1), dict) else mapping.get(1, "/dev/video0")
            right_dev = mapping.get(2, {}).get("device", "/dev/video2") if isinstance(mapping.get(2), dict) else mapping.get(2, "/dev/video2")

            cap_l = cv2.VideoCapture(get_cap_device(left_dev))
            cap_r = cv2.VideoCapture(get_cap_device(right_dev))
            if not cap_l.isOpened():
                asyncio.run_coroutine_threadsafe(
                    ws_ref.send(json.dumps({"type": "stereo_calib_result", "success": False, "message": "Camera gauche introuvable: " + left_dev})), loop)
                if cap_r.isOpened():
                    cap_r.release()
                return
            if not cap_r.isOpened():
                cap_l.release()
                asyncio.run_coroutine_threadsafe(
                    ws_ref.send(json.dumps({"type": "stereo_calib_result", "success": False, "message": "Camera droite introuvable: " + right_dev})), loop)
                return
            cap_l.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap_l.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            cap_r.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap_r.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

            _progress_sync(10, "Capture des paires synchronisees...")
            pat = (cols, rows)
            sq_m = square_mm / 1000.0
            objp = np.zeros((cols * rows, 3), np.float32)
            objp[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2) * sq_m
            objpts, il, ir = [], [], []
            cap_n, warm = 0, 0
            STABILITY_FRAMES_S = 8
            STABILITY_PX_S = 3.0
            DIVERSITY_CENTROID_PX_S = 35.0
            stable_count_l, stable_count_r = 0, 0
            prev_centroid_l, prev_centroid_r = None, None
            collected_centroids_l, collected_centroids_r = [], []
            found_any_s = False
            for attempt in range(num_pairs * 8):
                if config.stereo_calibration_cancel_event.is_set():
                    try:
                        asyncio.run_coroutine_threadsafe(
                            ws_ref.send(json.dumps({
                                "type": "stereo_calib_result",
                                "success": False, "message": "Calibration annulee."
                            })), loop
                        )
                    except Exception:
                        pass
                    return
                rl, fl = cap_l.read()
                rr, fr = cap_r.read()
                if not rl or not rr:
                    continue
                warm += 1
                if warm < 5:
                    continue
                gl = cv2.cvtColor(fl, cv2.COLOR_BGR2GRAY)
                gr = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
                mean_brightness_l = float(np.mean(gl))
                mean_brightness_r = float(np.mean(gr))
                flags_cb = cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE + cv2.CALIB_CB_FAST_CHECK
                fl_f, cl = cv2.findChessboardCorners(gl, pat, flags_cb)
                fr_f, cr = cv2.findChessboardCorners(gr, pat, flags_cb)
                
                # Stream frame preview to client via Base64 JPEG
                try:
                    import base64
                    fl_to_send = fl.copy()
                    fr_to_send = fr.copy()
                    if fl_f and fr_f:
                        cv2.drawChessboardCorners(fl_to_send, pat, cl, fl_f)
                        cv2.drawChessboardCorners(fr_to_send, pat, cr, fr_f)
                    
                    frame_to_send = np.hstack((fl_to_send, fr_to_send))
                    _, jpeg_buf = cv2.imencode('.jpg', frame_to_send, [cv2.IMWRITE_JPEG_QUALITY, 50])
                    jpeg_b64 = base64.b64encode(jpeg_buf).decode('utf-8')
                    asyncio.run_coroutine_threadsafe(
                        ws_ref.send(json.dumps({
                            "type": "stereo_calib_frame",
                            "image": jpeg_b64
                        })), loop
                    )
                except Exception as e_send_frame:
                    print(f"Error sending stereo calibration frame: {e_send_frame}")

                if fl_f and fr_f:
                    found_any_s = True
                    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
                    cv2.cornerSubPix(gl, cl, (11, 11), (-1, -1), crit)
                    cv2.cornerSubPix(gr, cr, (11, 11), (-1, -1), crit)
                    
                    c_l = np.mean(cl.reshape(-1, 2), axis=0)
                    c_r = np.mean(cr.reshape(-1, 2), axis=0)
                    
                    if prev_centroid_l is not None:
                        mov_l = float(np.linalg.norm(c_l - prev_centroid_l))
                        mov_r = float(np.linalg.norm(c_r - prev_centroid_r))
                    else:
                        mov_l, mov_r = 999.0, 999.0
                    
                    prev_centroid_l, prev_centroid_r = c_l, c_r
                    
                    if mov_l < STABILITY_PX_S and mov_r < STABILITY_PX_S:
                        stable_count_l += 1
                        stable_count_r += 1
                    else:
                        stable_count_l, stable_count_r = 0, 0
                    
                    stable = (stable_count_l >= STABILITY_FRAMES_S and stable_count_r >= STABILITY_FRAMES_S)
                    
                    if stable:
                        is_diverse = True
                        for past_c in collected_centroids_l:
                            if float(np.linalg.norm(c_l - past_c)) < DIVERSITY_CENTROID_PX_S:
                                is_diverse = False
                                break
                        
                        if is_diverse or cap_n == 0:
                            objpts.append(objp); il.append(cl); ir.append(cr)
                            collected_centroids_l.append(c_l)
                            collected_centroids_r.append(c_r)
                            cap_n += 1
                            stable_count_l, stable_count_r = 0, 0
                            pct = 10 + int(30 * cap_n / num_pairs)
                            _progress_sync(pct, f"Damier stable ✓ — {cap_n}/{num_pairs} paires...")
                            if cap_n >= num_pairs:
                                break
                    
                    if attempt % 20 == 0:
                        st = "✓ Stable" if stable else f"Stabilisation... {min(stable_count_l, stable_count_r)}/{STABILITY_FRAMES_S}"
                        _progress_sync(10, f"Damier detecte — {st}")
                else:
                    stable_count_l, stable_count_r = 0, 0
                    prev_centroid_l, prev_centroid_r = None, None
                    if attempt % 60 == 0:
                        if mean_brightness_l < 15.0 or mean_brightness_r < 15.0:
                            _progress_sync(10, "En attente du damier (⚠️ Image tres sombre)... Allumez la lumiere.")
                        else:
                            _progress_sync(10, "En attente du damier sur les 2 cameras...")
            # released in finally
            if cap_n < 5:
                asyncio.run_coroutine_threadsafe(
                    ws_ref.send(json.dumps({"type": "stereo_calib_result", "success": False, "message": f"Pas assez de paires ({cap_n})"})), loop)
                return

            _progress_sync(45, "Calibration mono gauche...")
            _, ml, dl, _, _ = cv2.calibrateCamera(objpts, il, gl.shape[::-1], None, None)
            _progress_sync(55, "Calibration mono droite...")
            _, mr, dr, _, _ = cv2.calibrateCamera(objpts, ir, gr.shape[::-1], None, None)
            _progress_sync(65, "cv2.stereoCalibrate...")
            criteria_s = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 100, 1e-5)
            ret_s, ml, dl, mr, dr, R, T, E, F = cv2.stereoCalibrate(
                objpts, il, ir, ml, dl, mr, dr, gl.shape[::-1],
                criteria=criteria_s, flags=cv2.CALIB_FIX_INTRINSIC)
            _progress_sync(75, "Rectification stereo...")
            R1, R2, P1, P2, Q, _, _ = cv2.stereoRectify(ml, dl, mr, dr, gl.shape[::-1], R, T, alpha=0)
            baseline = float(np.linalg.norm(T))
            camera_bf = float(ml[0, 0]) * baseline

            _progress_sync(85, "Sauvegarde des resultats...")
            stereo_data = {
                "image_width": gl.shape[1], "image_height": gl.shape[0],
                "camera_name": "usb_cam",
                "camera_matrix": ml.flatten().tolist(),
                "distortion_coefficients": dl.flatten().tolist(),
                "camera2_matrix": mr.flatten().tolist(),
                "camera2_distortion": dr.flatten().tolist(),
                "R": R.flatten().tolist(), "T": T.flatten().tolist(),
                "E": E.flatten().tolist(), "F": F.flatten().tolist(),
                "R1": R1.flatten().tolist(), "R2": R2.flatten().tolist(),
                "P1": P1.flatten().tolist(), "P2": P2.flatten().tolist(),
                "Q": Q.flatten().tolist(),
                "baseline_m": baseline, "camera_bf": camera_bf,
                "th_depth": 40.0, "is_calibrated": True,
                "reprojection_error": float(ret_s),
                "num_sample_pairs": cap_n,
                "calibrated_at": time.strftime("%d/%m/%Y %H:%M:%S")
            }
            try:
                url = f"{config.GATEWAY_URL}/core/camera/calibration/stereo"
                req = urllib.request.Request(url,
                    data=json.dumps(stereo_data).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-API-Token": config.API_TOKEN},
                    method="POST")
                with urllib.request.urlopen(req, context=config.ssl_ctx, timeout=10) as resp:
                    resp.read()
                print("[Agent] Calibration stereo sauvegardee sur la Gateway.")
            except Exception as e_post:
                print(f"[Agent] Erreur sauvegarde stereo Gateway: {e_post}")

            # Save YAML locally
            try:
                from camera import _json_calib_to_yaml_stereo, CAMERA_CALIB_STEREO_FILE
                yml = _json_calib_to_yaml_stereo(stereo_data)
                CAMERA_CALIB_STEREO_FILE.parent.mkdir(parents=True, exist_ok=True)
                with open(CAMERA_CALIB_STEREO_FILE, "w", encoding="utf-8") as f:
                    f.write(yml)
            except ImportError:
                pass

            _progress_sync(100, "Calibration stereo terminee !")
            asyncio.run_coroutine_threadsafe(
                ws_ref.send(json.dumps({
                    "type": "stereo_calib_result", "success": True,
                    "message": f"OK (reproj: {ret_s:.4f}, baseline: {baseline*1000:.1f}mm)",
                    "reprojection_error": float(ret_s),
                    "baseline_mm": round(baseline * 1000, 1),
                    "fx": round(float(ml[0, 0]), 2),
                    "num_pairs": cap_n
                })), loop
            )
        except Exception as e:
            import traceback; traceback.print_exc()
            try:
                asyncio.run_coroutine_threadsafe(
                    ws_ref.send(json.dumps({"type": "stereo_calib_result", "success": False, "message": str(e)})), loop)
            except Exception:
                pass
        finally:
            if cap_l is not None:
                try:
                    cap_l.release()
                    print("[Agent] VideoCapture gauche relache.")
                except Exception:
                    pass
            if cap_r is not None:
                try:
                    cap_r.release()
                    print("[Agent] VideoCapture droite relache.")
                except Exception:
                    pass
            start_spotbot_service()

    import threading
    threading.Thread(target=_stereo_calib_task, daemon=True).start()
