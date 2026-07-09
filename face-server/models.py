"""Pydantic models for Bastet Gateway API."""
from typing import Optional
from pydantic import BaseModel


class MyGESCredentials(BaseModel):
    username: str
    password: str


class CoreState(BaseModel):
    seen_person: Optional[str] = None
    seen_objects: list[str] = []
    last_chat: list[dict] = []
    robot_status: str = "idle"
    robot_version: Optional[str] = "v0.0.0"
    arduino_version: Optional[str] = "v0.0.0"
    sensors: dict = {}
    ai_state: dict = {}


class UpdateProgress(BaseModel):
    status: str
    percent: int


class AccountInfo(BaseModel):
    email: str
    pseudo: str
    last_name: str
    first_name: str
    phone: str
    password: Optional[str] = None
    password_hash: Optional[str] = None
    is_admin: bool = False
    preferences: dict = {}


class LoginRequest(BaseModel):
    email: str
    password: str


class PreferencesUpdate(BaseModel):
    full_name: str
    preferences: dict


class MonoCalibRequest(BaseModel):
    camera: int = 1
    chessboard_cols: int = 9
    chessboard_rows: int = 6
    square_size_mm: int = 25


class StereoCalibRequest(BaseModel):
    chessboard_cols: int = 9
    chessboard_rows: int = 6
    square_size_mm: int = 25


class NavGoalRequest(BaseModel):
    x: float
    y: float


class MotionVelocityRequest(BaseModel):
    linear: float
    angular: float


class MotionJointsRequest(BaseModel):
    angles: list[float]


class ArduinoCommandRequest(BaseModel):
    cmd: str
    index: Optional[int] = None
    angle: Optional[float] = None


class ChatRequest(BaseModel):
    text: str
