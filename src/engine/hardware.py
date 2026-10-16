"""
Hardware profiling and resource management utilities.
Analyzes host machine capabilities (CPU cores, RAM, OS architecture)
and configures multi-core parallel optimization.
"""
from __future__ import annotations
import os
import platform
import ctypes
from dataclasses import dataclass


@dataclass
class HardwareProfile:
    """System hardware capabilities profile."""
    cpu_count: int
    recommended_workers: int
    total_ram_gb: float
    free_ram_gb: float
    system_os: str
    machine: str

    @property
    def summary_short(self) -> str:
        """Short summary string."""
        if self.total_ram_gb > 0:
            return f"{self.cpu_count} núcleos CPU | {self.total_ram_gb:.1f} GB RAM"
        return f"{self.cpu_count} núcleos CPU detectados"

    @property
    def summary_detailed(self) -> str:
        """Detailed user-facing status string."""
        ram_part = f" — {self.total_ram_gb:.1f} GB RAM ({self.free_ram_gb:.1f} GB libre)" if self.total_ram_gb > 0 else ""
        return (
            f"Hardware: {self.cpu_count} núcleos lógicos ({self.machine}){ram_part}\n"
            f"Aceleración multinúcleo activada ({self.recommended_workers} procesos en paralelo — 100% CPU)"
        )


def get_hardware_profile() -> HardwareProfile:
    """Analyze host machine capabilities."""
    cpu_count = os.cpu_count() or 1
    total_ram_gb = 0.0
    free_ram_gb = 0.0

    # On Windows, query memory via GlobalMemoryStatusEx
    if platform.system() == "Windows":
        try:
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                total_ram_gb = round(stat.ullTotalPhys / (1024 ** 3), 1)
                free_ram_gb = round(stat.ullAvailPhys / (1024 ** 3), 1)
        except Exception:
            pass

    recommended = max(1, cpu_count)

    return HardwareProfile(
        cpu_count=cpu_count,
        recommended_workers=recommended,
        total_ram_gb=total_ram_gb,
        free_ram_gb=free_ram_gb,
        system_os=f"{platform.system()} {platform.release()}",
        machine=platform.machine()
    )
