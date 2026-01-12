import requests
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
import piexif
import tempfile
import shutil
import os


class ImageDownloaderWithExif:
    def __init__(self, image_data: dict, timezone: str = "UTC"):
        self.image_data = image_data
        self.id = image_data["id"]
        self.name = image_data["name"]
        self.src = image_data["src"]
        self.created_at = image_data["created_at"]
        self.location = image_data.get("location", {})
        self.timezone = ZoneInfo(timezone)

    # ---------- public API ----------

    def download_and_save(self, output_path: str) -> str:
        """
        Download the image, merge EXIF, and save to the given output path.
        Preserves original JPEG quality.
        """
        # Ensure output directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # Download raw JPEG bytes
        image_bytes = self._download_image_bytes()

        # Build EXIF
        exif_bytes = self._build_exif()

        # Use temporary file to insert EXIF
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(image_bytes)
            tmp.flush()
            tmp_name = tmp.name

        # Insert EXIF in-place
        piexif.insert(exif_bytes, tmp_name)

        # Move temp file to final output
        shutil.move(tmp_name, output_path)

        return output_path

    # ---------- internal helpers ----------

    def _download_image_bytes(self) -> bytes:
        url = self._strip_url_params(self.src)
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        return resp.content

    def _build_exif(self) -> bytes:
        created_exif = self._format_created_at_local()

        exif_dict = {
            "0th": {
                piexif.ImageIFD.ImageDescription: self.name.encode("utf-8"),
            },
            "Exif": {
                piexif.ExifIFD.DateTimeOriginal: created_exif,
                piexif.ExifIFD.DateTimeDigitized: created_exif,
            },
            "GPS": self._gps_exif(),
            "1st": {},
        }

        return piexif.dump(exif_dict)

    def _gps_exif(self) -> dict:
        if not self.location:
            return {}

        lat = self.location.get("lat")
        lng = self.location.get("lng")
        alt = self.location.get("alt", 0)

        return {
            piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S",
            piexif.GPSIFD.GPSLatitude: self._to_dms_rational(lat),
            piexif.GPSIFD.GPSLongitudeRef: b"E" if lng >= 0 else b"W",
            piexif.GPSIFD.GPSLongitude: self._to_dms_rational(lng),
            piexif.GPSIFD.GPSAltitudeRef: 0,
            piexif.GPSIFD.GPSAltitude: (int(alt * 100), 100),
        }

    # ---------- time handling ----------

    def _format_created_at_local(self) -> str:
        # Parse UTC
        dt_utc = datetime.strptime(
            self.created_at, "%Y-%m-%dT%H:%M:%S.%fZ"
        ).replace(tzinfo=ZoneInfo("UTC"))
        # Convert to target timezone
        dt_local = dt_utc.astimezone(self.timezone)
        # EXIF format (no timezone info)
        return dt_local.strftime("%Y:%m:%d %H:%M:%S")

    # ---------- static helpers ----------

    @staticmethod
    def _strip_url_params(url: str) -> str:
        return urlparse(url)._replace(query="").geturl()

    @staticmethod
    def _to_dms_rational(deg: float):
        deg_abs = abs(deg)
        d = int(deg_abs)
        m = int((deg_abs - d) * 60)
        s = (deg_abs - d - m / 60) * 3600
        return [(d, 1), (m, 1), (int(s * 100), 100)]
