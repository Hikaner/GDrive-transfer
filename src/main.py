import os
import sys
import uuid
import json
import signal
import subprocess
import secrets
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Cookie, Response, Query
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="GDrive Transfer Web-UI")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Generate a unique token for authentication
# If AUTH_TOKEN is set in environment (e.g. by Colab notebook), use it, otherwise generate a new one
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", secrets.token_hex(16))
CONFIG_PATH = os.path.abspath("rclone.conf")
LOG_PATH = os.path.abspath("transfer.log")

# Global variable to keep track of the active transfer process
active_process: Optional[subprocess.Popen] = None
transfer_status = {
    "status": "idle",  # idle, running, success, failed, stopped
    "error": None,
    "progress": {}
}

# Print the authentication URL for Google Colab users
print("\n" + "="*80)
print(f" GDrive-transfer Web-UI is starting...")
print(f" Access Token: {AUTH_TOKEN}")
print(f" Local URL: http://localhost:8000/?token={AUTH_TOKEN}")
print("="*80 + "\n")

# Dependency to verify token
def verify_token(
    token: Optional[str] = Query(None),
    auth_token: Optional[str] = Cookie(None)
):
    # Check query parameter first, then cookie
    current_token = token or auth_token
    if not current_token or current_token != AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid or missing token")
    return current_token

# Serve static files
# We will mount this after defining the API routes to avoid conflicts
# app.mount("/static", StaticFiles(directory="src/static"), name="static")

@app.get("/api/auth")
def authenticate(token: str, response: Response):
    if token == AUTH_TOKEN:
        # Set cookie valid for 1 day
        response.set_cookie(key="auth_token", value=token, max_age=86400, httponly=True, samesite="lax")
        return {"status": "authenticated"}
    raise HTTPException(status_code=401, detail="Invalid token")

@app.get("/api/config/status")
def get_config_status():
    exists = os.path.exists(CONFIG_PATH)
    remotes = []
    if exists:
        try:
            # Get list of remotes from rclone
            result = subprocess.run(
                ["rclone", "listremotes", "--config", CONFIG_PATH],
                capture_output=True,
                text=True,
                check=True
            )
            remotes = [r.strip().rstrip(":") for r in result.stdout.strip().split("\n") if r.strip()]
        except Exception as e:
            pass
    return {
        "config_exists": exists,
        "remotes": remotes
    }

@app.post("/api/config/upload")
async def upload_config(file: UploadFile = File(...), token: str = Depends(verify_token)):
    try:
        content = await file.read()
        # Basic validation: check if it looks like an ini file
        content_str = content.decode("utf-8", errors="ignore")
        if not content_str.strip().startswith("[") and content_str.strip() != "":
            raise HTTPException(status_code=400, detail="Invalid rclone.conf format. Must be an INI file.")
        
        with open(CONFIG_PATH, "wb") as f:
            f.write(content)
            
        # Verify remotes
        result = subprocess.run(
            ["rclone", "listremotes", "--config", CONFIG_PATH],
            capture_output=True,
            text=True
        )
        remotes = [r.strip().rstrip(":") for r in result.stdout.strip().split("\n") if r.strip()]
        
        return {
            "status": "success",
            "remotes": remotes
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save config: {str(e)}")

class ListPathRequest(BaseModel):
    remote: str
    path: str = ""

@app.post("/api/fs/list")
def list_path(req: ListPathRequest, token: str = Depends(verify_token)):
    if not os.path.exists(CONFIG_PATH):
        raise HTTPException(status_code=400, detail="rclone.conf not found. Please upload it first.")
    
    remote_path = f"{req.remote}:{req.path}"
    try:
        # Use rclone lsf with custom format and separator for compatibility with older rclone versions
        # Format: p (path), i (is_dir), s (size)
        # Separator: ;;
        cmd = [
            "rclone", "lsf",
            "--format", "pis",
            "--separator", ";;",
            "--config", CONFIG_PATH,
            remote_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            err_msg = result.stderr.strip() or "Unknown error listing path"
            raise HTTPException(status_code=400, detail=err_msg)
            
        formatted_items = []
        lines = result.stdout.strip().split("\n")
        for line in lines:
            if not line.strip():
                continue
            parts = line.split(";;")
            if len(parts) >= 3:
                name = parts[0].rstrip("/") # Remove trailing slash if any
                is_dir = parts[1].lower() == "true"
                try:
                    size = int(parts[2])
                except ValueError:
                    size = 0
                
                formatted_items.append({
                    "name": name,
                    "path": os.path.join(req.path, name).replace("\\", "/"),
                    "is_dir": is_dir,
                    "size": size,
                })
        return {"items": formatted_items}
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class TransferRequest(BaseModel):
    action: str  # copy, move, sync
    src_remote: str
    src_path: str
    dst_remote: str
    dst_path: str
    server_side: bool = True
    transfers: int = 4
    checkers: int = 8
    extra_args: List[str] = []

@app.post("/api/transfer/start")
def start_transfer(req: TransferRequest, token: str = Depends(verify_token)):
    global active_process, transfer_status
    
    if active_process and active_process.poll() is None:
        raise HTTPException(status_code=400, detail="A transfer process is already running.")
        
    if not os.path.exists(CONFIG_PATH):
        raise HTTPException(status_code=400, detail="rclone.conf not found. Please upload it first.")

    src = f"{req.src_remote}:{req.src_path}"
    dst = f"{req.dst_remote}:{req.dst_path}"
    
    # Build rclone command
    cmd = [
        "rclone", req.action,
        src, dst,
        "--config", CONFIG_PATH,
        "--transfers", str(req.transfers),
        "--checkers", str(req.checkers),
        "--use-json-log",
        "--stats", "1s",
        "--stats-one-line-date"
    ]
    
    if req.server_side:
        cmd.append("--drive-server-side-across-configs=true")
    else:
        cmd.append("--drive-server-side-across-configs=false")
        
    # Add extra arguments if any
    for arg in req.extra_args:
        if arg.strip():
            cmd.append(arg.strip())
            
    # Clear previous log
    if os.path.exists(LOG_PATH):
        try:
            os.remove(LOG_PATH)
        except:
            pass
            
    try:
        # Open log file for writing stderr (rclone outputs logs to stderr)
        log_file = open(LOG_PATH, "w")
        
        # Start process
        active_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=log_file,
            text=True,
            preexec_fn=None if sys.platform == "win32" else os.setsid
        )
        
        transfer_status = {
            "status": "running",
            "error": None,
            "progress": {
                "bytes": 0,
                "total_bytes": 0,
                "percentage": 0,
                "speed": "0 B/s",
                "eta": "Unknown",
                "transferred_files": 0,
                "total_files": 0,
                "active_transfers": []
            }
        }
        return {"status": "started"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start transfer: {str(e)}")

@app.post("/api/transfer/stop")
def stop_transfer(token: str = Depends(verify_token)):
    global active_process, transfer_status
    if not active_process or active_process.poll() is not None:
        return {"status": "not_running"}
        
    try:
        if sys.platform == "win32":
            active_process.terminate()
        else:
            os.killpg(os.getpgid(active_process.pid), signal.SIGTERM)
            
        active_process.wait(timeout=5)
        transfer_status["status"] = "stopped"
        return {"status": "stopped"}
    except Exception as e:
        # Force kill if terminate fails
        try:
            active_process.kill()
            transfer_status["status"] = "stopped"
            return {"status": "stopped"}
        except Exception as ke:
            raise HTTPException(status_code=500, detail=f"Failed to stop process: {str(ke)}")

def parse_rclone_log() -> Dict[str, Any]:
    global active_process, transfer_status
    
    if not os.path.exists(LOG_PATH):
        return transfer_status
        
    # Check if process has finished
    if active_process:
        ret_code = active_process.poll()
        if ret_code is not None:
            if ret_code == 0:
                transfer_status["status"] = "success"
            elif transfer_status["status"] == "running":
                transfer_status["status"] = "failed"
                transfer_status["error"] = f"Process exited with code {ret_code}"
                
    # Parse the log file (reading from end to find the latest stats)
    try:
        stats = {
            "bytes": 0,
            "total_bytes": 0,
            "percentage": 0,
            "speed": "0 B/s",
            "eta": "Unknown",
            "transferred_files": 0,
            "total_files": 0,
            "active_transfers": []
        }
        
        # Read lines from log file
        with open(LOG_PATH, "r") as f:
            lines = f.readlines()
            
        # Parse JSON logs from rclone
        # Rclone outputs JSON logs when --use-json-log is used
        # We look for the latest stats message
        for line in reversed(lines):
            try:
                data = json.loads(line)
                # Check if this is a stats message
                # In rclone, stats are usually logged under "stats" or contain specific keys
                if "stats" in data or ("msg" in data and "Transferred:" in data["msg"]):
                    # Sometimes rclone outputs stats as a structured object, sometimes as a message string
                    # Let's parse the message string if it's there
                    msg = data.get("msg", "")
                    # Example msg: "Transferred:   	         0 B / 0 B, -, 0 B/s, ETA -"
                    # Or we can parse the structured stats if available
                    # Let's check if there is a "stats" key
                    if "stats" in data:
                        s = data["stats"]
                        stats["bytes"] = s.get("bytes", 0)
                        stats["total_bytes"] = s.get("totalBytes", 0)
                        stats["percentage"] = s.get("percentage", 0)
                        stats["speed"] = s.get("speed", 0) # this might be bytes/sec
                        # Format speed
                        speed_val = s.get("speed", 0)
                        if speed_val > 1024*1024:
                            stats["speed"] = f"{speed_val / (1024*1024):.2f} MB/s"
                        elif speed_val > 1024:
                            stats["speed"] = f"{speed_val / 1024:.2f} KB/s"
                        else:
                            stats["speed"] = f"{speed_val} B/s"
                            
                        eta_val = s.get("eta", None)
                        stats["eta"] = f"{eta_val}s" if eta_val is not None else "Unknown"
                        stats["transferred_files"] = s.get("transfers", 0)
                        stats["total_files"] = s.get("totalTransfers", 0)
                        
                        # Active transfers
                        active = []
                        if "transferring" in s:
                            for t in s["transferring"]:
                                active.append({
                                    "name": t.get("name"),
                                    "size": t.get("size"),
                                    "bytes": t.get("bytes"),
                                    "percentage": t.get("percentage"),
                                    "speed": t.get("speed")
                                })
                        stats["active_transfers"] = active
                        break
            except:
                continue
                
        if stats["bytes"] > 0 or stats["total_bytes"] > 0:
            transfer_status["progress"] = stats
            
    except Exception as e:
        pass
        
    return transfer_status

@app.get("/api/transfer/status")
def get_transfer_status(token: str = Depends(verify_token)):
    return parse_rclone_log()

@app.get("/api/transfer/log")
def get_transfer_log(lines: int = 100, token: str = Depends(verify_token)):
    if not os.path.exists(LOG_PATH):
        return {"log": ""}
    try:
        with open(LOG_PATH, "r") as f:
            log_lines = f.readlines()
        # Return last N lines
        last_lines = log_lines[-lines:]
        # Try to make it readable (if it's JSON, we can extract the msg field or return raw)
        readable_lines = []
        for line in last_lines:
            try:
                data = json.loads(line)
                msg = data.get("msg", "")
                level = data.get("level", "info")
                time = data.get("time", "").split(".")[0].replace("T", " ")
                readable_lines.append(f"[{time}] [{level.upper()}] {msg}")
            except:
                readable_lines.append(line.strip())
        return {"log": "\n".join(readable_lines)}
    except Exception as e:
        return {"log": f"Error reading log: {str(e)}"}

# Serve index.html at root
@app.get("/")
def read_root(token: Optional[str] = None, auth_token: Optional[str] = Cookie(None)):
    # Check token to authorize access to the Web-UI
    current_token = token or auth_token
    if not current_token or current_token != AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid or missing token")
        
    static_index = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if os.path.exists(static_index):
        return FileResponse(static_index)
    return HTMLResponse("<h1>GDrive Transfer Web-UI</h1><p>Static files not found. Please build/create static files.</p>")

# Mount static files directory
# This allows serving app.js, style.css, etc.
static_dir = os.path.join(os.path.dirname(__file__), "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")
