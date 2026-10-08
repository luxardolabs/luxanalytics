"""Device schemas: what core hands the view about a device (its latest reported context)."""

from pydantic import BaseModel, ConfigDict


class DeviceRow(BaseModel):
    """A device's stored context, as of the last event it sent."""

    device_id: str
    device_model: str | None
    os_version: str | None
    app_version: str | None
    build_number: str | None
    screen_resolution: str | None
    locale: str | None
    timezone: str | None
    is_testflight: bool | None

    model_config = ConfigDict(from_attributes=True)
