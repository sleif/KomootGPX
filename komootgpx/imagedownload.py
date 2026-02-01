import requests
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
import piexif
import tempfile
import shutil
import os
from io import BytesIO
from PIL import Image
from .utils import *

class ImageDownloaderWithExif:
    def __init__(
        self,
        image_data: dict,
        api,
        no_poi,
        all_images,
        *,
        title: str = "",
        creator: str = "",
        timezone: str = "UTC",
        jpeg_quality: int = 90,
    ):
        self.api = api
        self.no_poi = no_poi
        self.all_images = all_images
        self.id = image_data["id"]
        self.name = image_data.get('name', title)
        self.src = image_data["src"]
        self.created_at = image_data["created_at"]
        self.location = image_data.get("location", {})
        self.creator_display_name = image_data.get('_embedded', {}).get('creator', {}).get('display_name', creator)
        self.highlight_id = image_data.get('highlight_id', None)
        self.timezone = ZoneInfo(timezone)
        self.jpeg_quality = jpeg_quality

    # ---------- public API ----------

    def download_and_save(self, output_path: str) -> str:
        image_bytes, is_png = self._download_image_bytes()
        if self.highlight_id:
            highlight = self.api.fetch_highlight(highlight_id=self.highlight_id, silent=True)
            self.name = highlight.get('base_name', '')
            self.creator_display_name = highlight.get('_embedded', {}).get('creator', {}).get('display_name', '')

        if is_png:
            image_bytes = self._png_to_jpeg(image_bytes)

        exif_bytes = self._build_exif()

        # piexif works on files only -> temp file
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(image_bytes)
            tmp.flush()
            tmp_name = tmp.name

        piexif.insert(exif_bytes, tmp_name)
        shutil.move(tmp_name, output_path)

        return output_path

    # ---------- downloading ----------

    def _download_image_bytes(self) -> tuple[bytes, bool]:
        url = self._strip_url_params(self.src)
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "").lower()
        is_png = "image/png" in content_type or url.lower().endswith(".png")

        return resp.content, is_png

    # ---------- PNG -> JPEG ----------

    def _png_to_jpeg(self, png_bytes: bytes) -> bytes:
        img = Image.open(BytesIO(png_bytes))

        # Handle transparency correctly
        if img.mode in ("RGBA", "LA"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1])
            img = background
        else:
            img = img.convert("RGB")

        out = BytesIO()
        img.save(
            out,
            format="JPEG",
            quality=self.jpeg_quality,
            subsampling=0,
            optimize=True,
        )
        return out.getvalue()

    # ---------- EXIF ----------

    def _build_exif(self) -> bytes:
        created_exif = self._format_created_at_local()

        exif_dict = {
            "0th": {
                **(
                    {piexif.ImageIFD.ImageDescription: self.name.encode("utf-8")}
                    if self.name
                    else {}
                ),
                **(
                    {piexif.ImageIFD.Artist: self.creator_display_name.encode("utf-8")}
                    if self.creator_display_name
                    else {}
                ),
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
        dt_utc = datetime.strptime(
            self.created_at, "%Y-%m-%dT%H:%M:%S.%fZ"
        ).replace(tzinfo=ZoneInfo("UTC"))

        dt_local = dt_utc.astimezone(self.timezone)
        return dt_local.strftime("%Y:%m:%d %H:%M:%S")

    # ---------- helpers ----------

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
