import cv2
import torch
import json
import os
import time
import threading
from facenet_pytorch import MTCNN, InceptionResnetV1
from PIL import Image
import base64
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
import numpy as np
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
from datetime import datetime, timedelta
from pydantic import BaseModel
from collections import defaultdict
import uvicorn
from typing import Dict
import asyncio
import logging
from fastapi import BackgroundTasks

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

latest_detection_data = None
logger = logging.getLogger(__name__)

# Removed RateLimiter class since we're not using automatic attendance anymore

# Remove the rate limiter since we're not doing automatic attendance
# attendance_limiter = RateLimiter(60)  # 60 second interval

class FaceDetect:
    def __init__(self, db_file="face_embeddings.json"):
        self.device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
        print(f'Running on device: {self.device}')

        # Initialize models
        self.mtcnn = MTCNN(keep_all=True, device=self.device)  # Detect multiple faces
        self.resnet = InceptionResnetV1(pretrained='vggface2').eval().to(self.device)

        # Load known embeddings
        self.db_file = db_file
        self.embeddings = self.load_embeddings()

        # Add batch processing capabilities
        self.batch_size = 32  # Adjust based on your GPU memory
    
    def load_embeddings(self):
        """Load face embeddings from a JSON file."""
        if os.path.exists(self.db_file):
            with open(self.db_file, "r") as f:
                return json.load(f)
        else:
            print(f"Embedding file {self.db_file} not found.")
            return {}

    def reload_embeddings(self):
        """Reload face embeddings from the JSON file."""
        try:
            if os.path.exists(self.db_file):
                with open(self.db_file, "r") as f:
                    self.embeddings = json.load(f)
                logger.info("Face embeddings reloaded successfully")
                return True
            else:
                logger.error(f"Embedding file {self.db_file} not found")
                return False
        except Exception as e:
            logger.error(f"Error reloading embeddings: {e}")
            return False

    def recognize_face(self, face_tensor):
        """Compares face embeddings to known faces in the database."""
        if face_tensor is None:
            return "Unknown", None

        if len(face_tensor.shape) == 3:  # Ensure correct shape
            face_tensor = face_tensor.unsqueeze(0)

        embedding = self.resnet(face_tensor.to(self.device)).detach().cpu()

        min_dist = float('inf')
        identity = "Unknown"

        for name, encodings in self.embeddings.items():
            for db_enc in encodings:
                dist = torch.nn.functional.pairwise_distance(
                    embedding,
                    torch.tensor(db_enc).unsqueeze(0)
                )

                if dist.numel() == 1:
                    dist = dist.item()
                else:
                    dist = dist.mean().item()

                if dist < min_dist:
                    min_dist = dist
                    identity = name

        threshold = 0.6
        return (identity, min_dist) if min_dist <= threshold else ("Unknown", min_dist)

    async def process_frame(self, frame):
        """Process a video frame, detect faces, and recognize identities."""
        global latest_detection_data, latest_detection_times

        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        boxes, _ = self.mtcnn.detect(img)

        detection_data = {
            "identity": "Unknown",
            "detection_times": {},
            "confidence": None
        }

        if boxes is not None and len(boxes) > 0:
            largest_box = max(boxes, key=lambda box: (box[2] - box[0]) * (box[3] - box[1]))
            x1, y1, x2, y2 = map(int, largest_box)

            face_img = img.crop((x1, y1, x2, y2))
            face_tensor = self.mtcnn(face_img)

            if face_tensor is not None:
                face_tensor = face_tensor.unsqueeze(0) if len(face_tensor.shape) == 3 else face_tensor
                identity, dist = self.recognize_face(face_tensor)

                if identity != "Unknown":
                    detection_time = time.strftime("%H:%M:%S", time.localtime())
                    confidence = 1 - dist

                    detection_data = {
                        "identity": identity,
                        "detection_times": {identity: {"time": detection_time, "confidence": confidence}},
                        "confidence": confidence
                    }

                    # ✅ Fix: Update latest_detection_times properly
                    latest_detection_times[identity] = {
                        "time": detection_time,
                        "confidence": confidence
                    }

                    # Debugging Print Statements
                    print(f"Detected: {identity}, Confidence: {confidence}")
                    print(f"Updated latest_detection_times: {latest_detection_times}")

        # ✅ Update global latest_detection_data
        latest_detection_data = {
            "frame": frame,
            "identity": detection_data["identity"],
            "detection_times": detection_data["detection_times"],
            "confidence": detection_data["confidence"],
            "last_detection": time.strftime("%H:%M:%S", time.localtime())
        }

        # Removed automatic attendance processing - attendance only through UI buttons

        # Instead of return, we'll update these values directly
        latest_frame = frame
        latest_detected_ids = detection_data["identity"]
        latest_detection_times = detection_data["detection_times"]

    def encode_image(self, frame):
        """Convert frame to base64 encoded string."""
        _, buffer = cv2.imencode('.jpeg', frame)
        img_bytes = buffer.tobytes()
        # return base64.b64encode(img_bytes).decode('utf-8')
        return img_bytes

    async def process_embeddings(self, image_paths):
        """Process multiple images in batches"""
        embeddings = []

        # Process in batches
        for i in range(0, len(image_paths), self.batch_size):
            batch_paths = image_paths[i:i + self.batch_size]
            batch_images = []

            # Load and preprocess images
            for path in batch_paths:
                try:
                    img = Image.open(path)
                    face = self.mtcnn(img)
                    if face is not None:
                        batch_images.append(face)
                except Exception as e:
                    logger.error(f"Error processing image {path}: {e}")
                    continue

            if batch_images:
                # Stack tensors for batch processing
                batch_tensor = torch.stack(batch_images)

                # Get embeddings for batch
                with torch.no_grad():  # Disable gradient calculation
                    batch_embeddings = self.resnet(batch_tensor.to(self.device))

                embeddings.extend(batch_embeddings.cpu().numpy())

        return embeddings


face_detector = FaceDetect()

latest_frame = None
latest_detected_ids = []
latest_detection_times = {}
# Removed automatic attendance variables since we're only using manual attendance

def reset_camera():
    """Force reset the Jetson camera pipeline to fix stream issues."""
    os.system("systemctl restart nvargus-daemon")
    time.sleep(2)  # Give time for the daemon to restart

checked_status = None

employee_check_status  = {}

async def process_frame_wrapper(frame):
    try:
        async for _ in face_detector.process_frame(frame):
            pass
    except Exception as e:
        print(f"Error in process_frame: {e}")

# Replace the current video_capture function with this updated version
def video_capture():
    """Continuously capture and process video frames."""
    global latest_frame, latest_detected_ids, latest_detection_times

    # Create event loop for this thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # pipeline = (
        # "nvarguscamerasrc sensor-id=0 sensor-mode=3 ! "
    #     "video/x-raw(memory:NVMM), width=1920, height=1080, format=NV12, framerate=30/1 ! "
    #     "nvvidconv ! video/x-raw, format=BGRx ! "
    #     "videoconvert ! video/x-raw, format=BGR ! "
    #     "appsink drop=1"
    # )

    # cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
    cap = cv2.VideoCapture(0)
    # cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    # cap.set(cv2.CAP_PROP_FPS, 30)
    # cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    # cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    if not cap.isOpened():
        print("\u274c Could not open camera. Exiting.")
        return

    try:
        while True:
            try:
                ret, frame = cap.read()
                if not ret:
                    print("\u26a0\ufe0f Warning: Failed to grab frame.")
                    # reset_camera()
                    cap.release()
                    time.sleep(2)
                    # cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
                    cap = cv2.VideoCapture(0)
                    continue

                # Use the event loop to run async functions
                loop.run_until_complete(process_frame_wrapper(frame))

                # if employee_check_status:
                #     if employee_check_status[str(latest_detected_ids)]:
                #         loop.run_until_complete(check_in())
                #     else:
                #         loop.run_until_complete(check_out())

            except Exception as e:
                print(f"\u26a0\ufe0f Error processing frame: {str(e)}")
                time.sleep(1)

    except KeyboardInterrupt:
        print("Stopping video capture...")
    except Exception as e:
        print(f"\u274c Fatal error during capture: {str(e)}")
    finally:
        if 'cap' in locals() and cap is not None:
            cap.release()
            cv2.destroyAllWindows()
        loop.close()
        print("\U0001f504 Camera resources released.")

# def generate_video_stream():
#     """Generate a continuous video stream with detection results."""
#     while True:
#         try:
#             if latest_frame is not None:
#                 # encoded_frame = face_detector.encode_image(latest_frame)
#                 _, encoded_frame = cv2.imencode('jpeg', latest_frame)
                
#                 frame_data = {
#                     "type": "frame_update",
#                     "data": {
#                         "image": encoded_frame.tobytes(),
#                         "detection": latest_detected_ids,
#                         "detection_times": latest_detection_times,
#                         "timestamp": time.time()
#                     }
#                 }
#                 yield f"data: {json.dumps(frame_data)}\n\n"
            
#             # Add a small delay to control frame rate
#             time.sleep(0.033)  # ~30 FPS
            
#         except Exception as e:
#             print(f"Error in video stream: {e}")
#             # Send error event to client
#             error_data = {
#                 "type": "error",
#                 "data": {"message": "Video stream error occurred"}
#             }
#             yield f"data: {json.dumps(error_data)}\n\n"
#             time.sleep(1)  # Wait before retrying
        
def generate_video_stream():
    """Generate a continuous MJPEG stream with detection results."""
    while True:
        try:
            if latest_frame is not None:
                # Convert frame to JPEG format
                _, jpeg = cv2.imencode('.jpg', latest_frame)
                frame_bytes = jpeg.tobytes()
                
                # Use multipart/x-mixed-replace format for MJPEG streaming
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
            
            # Small delay to control frame rate
            time.sleep(0.033)  # ~30 FPS
            
        except Exception as e:
            print(f"Error in video stream: {e}")
            time.sleep(1)  # Wait before retrying

def save_attendance(emp_id, detection_time, check_type):
    try:
        conn = sqlite3.connect(r"../db.sqlite3")
        cursor = conn.cursor()

        # Get current date
        current_date = datetime.now().strftime("%Y-%m-%d")

        # Check if the employee already has an attendance record for today
        cursor.execute("SELECT emp_id, time_in_list, time_out_list FROM Home_attendance WHERE emp_id = ? AND date = ?",
                    (emp_id, current_date))
        record = cursor.fetchone()

        if record:
            # Record exists, update time_in or time_out by appending new time
            attendance_id, time_in, time_out = record

            if check_type == "check_in":
                if time_in:
                    updated_time_in = f"{time_in},{detection_time}"
                else:
                    updated_time_in = detection_time
                cursor.execute("UPDATE Home_attendance SET time_in_list = ? WHERE id = ?", (updated_time_in, attendance_id))
            elif check_type == "check_out":
                if time_out:
                    updated_time_out = f"{time_out},{detection_time}"
                else:
                    updated_time_out = detection_time
                cursor.execute("UPDATE Home_attendance SET time_out_list = ? WHERE id = ?", (updated_time_out, attendance_id))

        else:
            # Insert a new record with the first detection time
            if check_type == "check_in":
                cursor.execute(
                    "INSERT INTO Home_attendance (date, emp_id, time_in_list, time_out_list) VALUES (?, ?, ?, ?)",
                    (current_date, emp_id, detection_time, ''))
            elif check_type == "check_out":
                cursor.execute(
                    "INSERT INTO Home_attendance (date, emp_id, time_in_list, time_out_list) VALUES (?, ?, ?, ?)",
                    (current_date, emp_id, '', detection_time))

        conn.commit()
    except sqlite3.Error as e:
            print(f"Database error: {e}")
            raise HTTPException(status_code=500, detail="Database connection failed")
    finally:
        if conn:
            conn.close()

class EmbeddingRequest(BaseModel):
    db_path: str
    output_file: str = "face_embeddings.json"


def store_embeddings(db_path, output_file):
    if not os.path.exists(db_path):
        return {"error": "Dataset path does not exist"}

    existing_embeddings = {}
    if os.path.exists(output_file):
        with open(output_file, 'r') as f:
            existing_embeddings = json.load(f)

    embeddings = defaultdict(list, existing_embeddings)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    mtcnn = MTCNN(image_size=160, margin=0, keep_all=False, device=device)
    resnet = InceptionResnetV1(pretrained='vggface2').eval().to(device)

    for identity in os.listdir(db_path):
        # Skip the profile_pics directory
        if identity == 'profile_pics':
            continue
            
        identity_path = os.path.join(db_path, identity)
        if os.path.isdir(identity_path):
            for image_name in os.listdir(identity_path):
                image_path = os.path.join(identity_path, image_name)
                try:
                    img = Image.open(image_path).convert('RGB')
                    img_cropped = mtcnn(img)
                    if img_cropped is not None:
                        img_embedding = resnet(img_cropped.unsqueeze(0).to(device)).detach().cpu().numpy().tolist()
                        embeddings[identity].append(img_embedding)
                except Exception as e:
                    print(f"Error processing {image_path}: {str(e)}")
                    continue

    with open(output_file, "w") as f:
        json.dump(dict(embeddings), f, indent=4)

    return {"message": "Embeddings stored successfully", "output_file": output_file}


@app.post("/store_embeddings/")
def api_store_embeddings(request: EmbeddingRequest):
    return store_embeddings(request.db_path, request.output_file)


@app.get("/load_embeddings/")
def load_embeddings(input_file: str = "face_embeddings.json"):
    if not os.path.exists(input_file):
        return {"error": "Embeddings file not found"}

    with open(input_file, "r") as f:
        embeddings = json.load(f)
    return embeddings


@app.get('/check-in')
async def check_in():
    global latest_detection_times

    print(f"latest_detection_times: {latest_detection_times}")  # Debugging
    if not latest_detection_times:
        raise HTTPException(status_code=400, detail="No face detected")

    # ✅ Fix: Properly get the latest detected employee ID
    emp_id, detection_data = next(iter(latest_detection_times.items()))
    employee_check_status[str(emp_id)]=True

    db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'db.sqlite3'))
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT emp_name, department FROM Home_employee WHERE emp_id = ?", (emp_id,))
    employee = cursor.fetchone()
    conn.close()

    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    name, department = employee

    print(f"Detected emp_id: {emp_id}, Confidence: {detection_data['confidence']}")  # Debugging

    if detection_data["confidence"] < 0.4:
        raise HTTPException(status_code=400, detail="Face recognition confidence too low")

    # Only save attendance when button is clicked
    save_attendance(emp_id, detection_data["time"], "check_in")

    return {
        "status": "success",
        "message": "Checked in successfully",
        "employee": {
            "name":name,
            "id": emp_id,
            "department":department,
            "confidence": f"{detection_data['confidence']:.2%}",
            "time": detection_data["time"]
        }
    }


@app.get('/check-out')
async def check_out():
    global latest_detection_times

    print(f"latest_detection_times: {latest_detection_times}")  # Debugging

    if not latest_detection_times:
        raise HTTPException(status_code=400, detail="No face detected")

    # ✅ Fix: Properly get the latest detected employee ID
    emp_id, detection_data = next(iter(latest_detection_times.items()))
    employee_check_status[str(emp_id)]=False

    db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'db.sqlite3'))
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT emp_name, department FROM Home_employee WHERE emp_id = ?", (emp_id,))
    employee = cursor.fetchone()
    conn.close()

    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    name, department = employee

    print(f"Detected emp_id: {emp_id}, Confidence: {detection_data['confidence']}")  # Debugging

    if detection_data["confidence"] < 0.4:  # Higher threshold for check-out
        raise HTTPException(status_code=400, detail="Face recognition confidence too low")

    # Only save attendance when button is clicked
    save_attendance(emp_id, detection_data["time"], "check_out")

    return {
        "status": "success",
        "message": "Checked out successfully",
        "employee": {
           "name":name,
            "id": emp_id,
            "department":department,
            "confidence": f"{detection_data['confidence']:.2%}",
            "time": detection_data["time"]
        }
    }


@app.get('/video_stream')
async def video_stream():
    """Stream video with face detection results to the client."""
    return StreamingResponse(generate_video_stream(), media_type='text/event-stream')


# Removed automatic attendance processing functions since we only use manual button clicks
# The process_automatic_attendance function is no longer needed

def calculate_working_hours(emp_id: str) -> str:
    """Calculate working hours for an employee"""
    try:
        conn = sqlite3.connect(r"../db.sqlite3")
        cursor = conn.cursor()
        current_date = datetime.now().strftime("%Y-%m-%d")
        
        cursor.execute("""
            SELECT time_in_list, time_out_list 
            FROM Home_attendance 
            WHERE emp_id = ? AND date = ?
        """, (emp_id, current_date))
        
        record = cursor.fetchone()
        if not record:
            return "--:--"
            
        time_in, time_out = record
        
        if not time_in or not time_out:
            return "--:--"
            
        # Get latest check-in and check-out times
        latest_in = time_in.split(',')[-1]
        latest_out = time_out.split(',')[-1]
        
        # Convert to datetime
        time_in_dt = datetime.strptime(latest_in, "%H:%M:%S")
        time_out_dt = datetime.strptime(latest_out, "%H:%M:%S")
        
        # Calculate duration
        duration = time_out_dt - time_in_dt
        hours = duration.seconds // 3600
        minutes = (duration.seconds % 3600) // 60
        
        return f"{hours:02d}:{minutes:02d}"
        
    except Exception as e:
        print(f"Error calculating working hours: {e}")
        return "--:--"
    finally:
        if conn:
            conn.close()

class AttendanceError(Exception):
    """Custom exception for attendance-related errors"""
    pass

def validate_attendance(emp_id: str, check_type: str) -> bool:
    """Validate attendance marking conditions - simplified for manual attendance only"""
    try:
        conn = sqlite3.connect(r"../db.sqlite3")
        cursor = conn.cursor()
        
        # Check if employee exists
        cursor.execute("SELECT emp_id FROM Home_employee WHERE emp_id = ?", (emp_id,))
        if not cursor.fetchone():
            raise AttendanceError("Employee not found")
            
        # For manual attendance, we allow immediate check-in/out without time restrictions
        return True
        
    except AttendanceError as e:
        print(f"Attendance validation failed: {e}")
        return False
    finally:
        if conn:
            conn.close()

def get_db_connection(max_retries=3, retry_delay=1):
    """Get database connection with retry mechanism"""
    for attempt in range(max_retries):
        try:
            conn = sqlite3.connect(r"../db.sqlite3")
            return conn
        except sqlite3.Error as e:
            if attempt == max_retries - 1:
                logger.error(f"Failed to connect to database after {max_retries} attempts")
                raise
            logger.warning(f"Database connection attempt {attempt + 1} failed, retrying...")
            time.sleep(retry_delay)


@app.post("/reload-embeddings")
async def reload_embeddings(background_tasks: BackgroundTasks):
    """Endpoint to reload face embeddings"""
    try:
        success = face_detector.reload_embeddings()
        if success:
            return {"status": "success", "message": "Face embeddings reloaded successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to reload embeddings")
    except Exception as e:
        logger.error(f"Error in reload endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))

            
if __name__ == '__main__':
    # Start video processing in a separate thread
    video_thread = threading.Thread(target=video_capture, daemon=True)
    video_thread.start()

    # Start the FastAPI server
    uvicorn.run(app, host="0.0.0.0", port=5600)

