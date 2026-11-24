"""Shared identity, links and availability."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_SERIAL, DOMAIN
from .coordinator import RidenCoordinator


class RidenEntity(CoordinatorEntity[RidenCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: RidenCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self.key = key
        self.serial = str(coordinator.entry.data[CONF_SERIAL])
        self._attr_unique_id = f"{self.serial}_{key}"
        host = coordinator.entry.data["host"]
        webhost = f"[{host}]" if ":" in host else host
        self.base_url = f"http://{webhost}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.serial)},
            manufacturer="Riden",
            model="RD6006P",
            name=coordinator.entry.title,
            serial_number=self.serial,
            sw_version=f"PSU raw {coordinator.client.identity.get('firmware', '?')}",
            configuration_url=f"{self.base_url}/control/",
        )

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "rd6006p_serial": self.serial,
            "rd6006p_key": self.key,
            "web_control_url": f"{self.base_url}/control/",
            "diagnostics_url": f"{self.base_url}/diagnostics/modbus",
        }
