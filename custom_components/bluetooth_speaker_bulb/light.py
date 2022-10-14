""" light platform """
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.light import (
    ATTR_EFFECT,
    ATTR_BRIGHTNESS,
    ATTR_HS_COLOR,
    ATTR_WHITE,
    ENTITY_ID_FORMAT,
    PLATFORM_SCHEMA,
    SUPPORT_BRIGHTNESS,
    SUPPORT_COLOR,
    SUPPORT_EFFECT,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC, CONF_NAME, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import generate_entity_id
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.color import (
    color_hs_to_RGB,
    color_RGB_to_hs
)

from .const import DOMAIN
from bleak import BleakError
from bluetooth_speaker_bulb import Bulb

if TYPE_CHECKING:
    from bleak.backends.device import BLEDevice

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_MAC): cv.string,
        vol.Optional(CONF_NAME, default=DOMAIN): cv.string,
    }
)

SUPPORT_MYLIGHT = SUPPORT_BRIGHTNESS | SUPPORT_EFFECT | SUPPORT_COLOR

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the platform from config_entry."""
    _LOGGER.debug(
        f"light async_setup_entry: setting up the config entry {config_entry.title} "
        f"with data:{config_entry.data}"
    )
    name = config_entry.data.get(CONF_NAME) or DOMAIN
    ble_device = hass.data[DOMAIN][config_entry.entry_id]

    entity = BulbBT(name, ble_device)
    async_add_entities([entity])


class BulbBT(LightEntity):
    """Representation of a light."""

    def __init__(self, name: str, ble_device: BLEDevice) -> None:
        """Initialize the light."""
        self._name = name
        self._mac = ble_device.address
        self.entity_id = generate_entity_id(ENTITY_ID_FORMAT, self._name, [])
        # self._model = model_from_name(self._ble_device.name)
        self._is_on = False
        self._rgb = [0, 0, 0]
        self._brightness = 0
        self._white = None
        self._white_intensity = None
        self._effect = None
        self._available = False
        self._versions: str | None = None

        _LOGGER.info(f"Initializing BulbBT Entity: {self.name}, {self._mac}")
        self._dev = Bulb(ble_device)
        self._dev._connection.add_callback_on_state_changed(self._status_cb)
        self._effect_list = self._dev.get_light_effects()

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        self.async_on_remove(
            self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STOP, self.async_will_remove_from_hass()
            )
        )
        _LOGGER.debug("BULB: before first connection ----")
        # execute a first connection to get the device model
        await self._dev.connect()
        _LOGGER.debug("BULB: after first connection ----")

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity will be removed from hass."""
        _LOGGER.debug("Running async_will_remove_from_hass")
        try:
            await self._dev.disconnect()
        except BleakError:
            _LOGGER.debug(
                f"Exception disconnecting from {self._dev._connection._mac}", exc_info=True
            )

    @property
    def device_info(self) -> dict[str, Any]:
        # TODO: replace with _attr
        prop = {
            "identifiers": {
                # Serial numbers are unique identifiers within a specific domain
                (DOMAIN, self.unique_id)
            },
            "name": self._name,
            "manufacturer": "Bluetooth Speaker Bulb",
            "model": "I'm a festive light bulb speaker",
        }
        if self._versions:
            prop.update(
                {"sw_version": "-".join(map(str, self._versions[1:4]))})
        return prop

    @property
    def unique_id(self) -> str:
        # TODO: replace with _attr
        """Return the unique id of the light."""
        return self._mac

    @property
    def available(self) -> bool:
        return self._available

    @property
    def should_poll(self) -> bool:
        """Polling needed for a updating status."""
        return True

    @property
    def name(self) -> str:
        """Return the name of the light if any."""
        return self._name

    @property
    def brightness(self) -> int:
        """Return the brightness of this light between 0..255."""
        return self._brightness

    @property
    def hs_color(self) -> tuple[Any]:
        """
        Return the Hue and saturation color value.
        Bulb has rgb => we calculate hs
        """
        return color_RGB_to_hs(*self._rgb)

    @property
    def white_intesity(self) -> int:
        """Return the white intensity."""
        return self._white_intensity

    @property
    def effect_list(self):
        """Return the list of supported effects."""
        return self._effect_list

    @property
    def effect(self):
        """Return the current effect."""
        return self._effect

    @property
    def is_on(self) -> bool:
        """Return true if light is on."""
        return self._is_on

    @property
    def supported_features(self) -> int:
        """Flag supported features."""
        return SUPPORT_MYLIGHT

    def _status_cb(self) -> None:
        _LOGGER.debug("Got state notification from the Bulb")
        self._available = self._dev._connection._client.is_connected
        if not self._available:
            self.async_write_ha_state()
            return

        self._is_on = self._dev._light.on
        if self._is_on:
            self._brightness = self._dev._light.brightness
            self._white = self._dev._light.white
            if self._white:
                self._white_intensity = self._dev._light._white_intensity
                self._rgb = [0, 0, 0]
            else:
                self._white_intensity = None
                self._rgb = self._dev._light._rgb

        self.async_write_ha_state()

    async def async_update(self) -> None:
        # Note, update should only start fetching,
        # followed by asynchronous updates through notifications.
        try:
            _LOGGER.debug("Requesting an update of the Bulb status")
            await self._dev.update_light()

        except Exception as ex:
            _LOGGER.error(
                f"Fail requesting the light status. Got exception: {ex}")
            _LOGGER.debug("BulbBT trace:", exc_info=True)

    async def async_turn_on(self, **kwargs: int) -> None:
        """Turn the light on."""
        _LOGGER.debug(f"Trying to turn on. with ATTR:{kwargs}")

        # First if brightness of dev to 0: turn off
        if ATTR_BRIGHTNESS in kwargs:
            brightness = kwargs[ATTR_BRIGHTNESS]
            if brightness == 0:
                _LOGGER.debug(
                    "Bulb brightness to be set to 0... so turning off")
                await self.async_turn_off()
                return
        else:
            brightness = self._brightness
        brightness_dev = brightness

        # ATTR cannot be set while light is off, so turn it on first
        if not self._is_on:
            await self._dev.turn_on()
            if any(
                keyword in kwargs
                for keyword in (ATTR_HS_COLOR, ATTR_WHITE, ATTR_BRIGHTNESS)
            ):
                await asyncio.sleep(0.7)  # wait for the Bulb to turn on
        self._is_on = True

        if ATTR_HS_COLOR in kwargs:
            rgb: tuple[int, int, int] = color_hs_to_RGB(
                *kwargs.get(ATTR_HS_COLOR))
            _LOGGER.debug(
                f"Trying to set color RGB:{rgb}"
            )
            await self._dev.set_color_rgb([*rgb])
            # assuming new state before Bulb update comes through:
            self._white = False
            self._rgb = [*rgb]
            # give time to transition before HA request update
            await asyncio.sleep(0.7)
            # _LOGGER.debug(f"Trying to set brightness: {brightness_dev}")
            # await self._dev.set_brightness(brightness_dev)
            # # assuming new state before Bulb update comes through:
            # self._brightness = brightness_dev
            # # give time to transition before HA request update
            # await asyncio.sleep(0.7)
            return

        if ATTR_WHITE in kwargs:
            self._white_intensity = kwargs[ATTR_WHITE]
            _LOGGER.debug(
                f"Trying to white intensity:{self._white}"
            )
            await self._dev.set_white_intensity(kwargs[ATTR_WHITE])
            # assuming new state before Bulb update comes through:
            self._white = True
            self._rgb = [0, 0, 0]
            # give time to transition before HA request update
            await asyncio.sleep(0.7)
            # _LOGGER.debug(f"Trying to set brightness: {brightness_dev}")
            # await self._dev.set_brightness(brightness_dev)
            # # give time to transition before HA request update
            # await asyncio.sleep(0.7)
            return

        if ATTR_BRIGHTNESS in kwargs:
            _LOGGER.debug(f"Trying to set brightness: {brightness_dev}")
            await self._dev.set_brightness(brightness_dev)
            # assuming new state before Bulb update comes through:
            self._brightness = brightness_dev
            # give time to transition before HA request update
            await asyncio.sleep(0.7)
            return

        if ATTR_EFFECT in kwargs:
            self._effect = kwargs[ATTR_EFFECT]
            if self._effect == 0 or self._effect is None:
                await self._dev.set_white()
                # give time to transition before HA request update
                await asyncio.sleep(0.7)
                return
            else:
                await self._dev.set_effect(self._effect)
                # give time to transition before HA request update
            await asyncio.sleep(0.7)

    async def async_turn_off(self, **kwargs: int) -> None:
        """Turn the light off."""

        await self._dev.turn_off()
        self._is_on = False
