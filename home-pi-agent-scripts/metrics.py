import os

def get_system_metrics() -> dict:
    """Récupère les métriques système du Raspberry Pi (charge CPU, RAM, Température)."""
    metrics = {
        "cpu_temp": 0.0,
        "cpu_load_1m": 0.0,
        "ram_total_mb": 0,
        "ram_used_mb": 0,
        "ram_percent": 0.0
    }
    
    # Température CPU
    try:
        if os.path.exists("/sys/class/thermal/thermal_zone0/temp"):
            temp_raw = open("/sys/class/thermal/thermal_zone0/temp").read().strip()
            metrics["cpu_temp"] = round(int(temp_raw) / 1000.0, 1)
    except Exception:
        pass
        
    # Charge CPU (1 min)
    try:
        if os.path.exists("/proc/loadavg"):
            load_raw = open("/proc/loadavg").read().strip().split()
            metrics["cpu_load_1m"] = float(load_raw[0])
    except Exception:
        pass
        
    # Mémoire RAM
    try:
        if os.path.exists("/proc/meminfo"):
            meminfo = {}
            for line in open("/proc/meminfo"):
                parts = line.split()
                if len(parts) >= 2:
                    meminfo[parts[0].rstrip(":")] = int(parts[1])
                    
            total = meminfo.get("MemTotal", 0) // 1024
            available = meminfo.get("MemAvailable", 0) // 1024
            used = total - available
            
            metrics["ram_total_mb"] = total
            metrics["ram_used_mb"] = used
            metrics["ram_percent"] = round((used / total) * 100.0, 1) if total > 0 else 0.0
    except Exception:
        pass
        
    return metrics
