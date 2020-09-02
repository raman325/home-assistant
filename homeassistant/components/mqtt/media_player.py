"""Support for MQTT media player devices."""
import logging

import voluptuous as vol

from homeassistant.components import mqtt
from homeassistant.components.media_player import (
    PLATFORM_SCHEMA as MEDIA_PLAYER_PLATFORM_SCHEMA,
    MediaPlayerEntity,
)
from homeassistant.components.media_player.const import (
    DOMAIN as MP_DOMAIN,
    SUPPORT_CLEAR_PLAYLIST,
    SUPPORT_NEXT_TRACK,
    SUPPORT_PAUSE,
    SUPPORT_PLAY,
    SUPPORT_PLAY_MEDIA,
    SUPPORT_PREVIOUS_TRACK,
    SUPPORT_SEEK,
    SUPPORT_SELECT_SOUND_MODE,
    SUPPORT_SELECT_SOURCE,
    SUPPORT_SHUFFLE_SET,
    SUPPORT_STOP,
    SUPPORT_TURN_OFF,
    SUPPORT_TURN_ON,
    SUPPORT_VOLUME_MUTE,
    SUPPORT_VOLUME_SET,
)
from homeassistant.const import (
    CONF_DEVICE,
    CONF_NAME,
    CONF_UNIQUE_ID,
    CONF_VALUE_TEMPLATE,
    STATE_OFF,
    STATE_ON,
    STATE_PLAYING,
)
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.typing import ConfigType, HomeAssistantType

from . import (
    ATTR_DISCOVERY_HASH,
    CONF_QOS,
    CONF_RETAIN,
    MQTT_BASE_PLATFORM_SCHEMA,
    MqttAttributes,
    MqttAvailability,
    MqttDiscoveryUpdate,
    MqttEntityDeviceInfo,
    subscription,
)
from .debug_info import log_messages
from .discovery import MQTT_DISCOVERY_NEW, clear_discovery_hash

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "MQTT Media Player"

CONF_APP_ID_TEMPLATE = "app_id_template"
CONF_APP_ID_TOPIC = "app_id_topic"
CONF_APP_NAME_TEMPLATE = "app_name_template"
CONF_APP_NAME_TOPIC = "app_name_topic"
CONF_ENTITY_PICTURE_TEMPLATE = "entity_picture_template"
CONF_ENTITY_PICTURE_TOPIC = "entity_picture_topic"
CONF_MEDIA_ALBUM_ARTIST_TEMPLATE = "media_album_artist_template"
CONF_MEDIA_ALBUM_ARTIST_TOPIC = "media_album_artist_topic"
CONF_MEDIA_ALBUM_NAME_TEMPLATE = "media_album_name_template"
CONF_MEDIA_ALBUM_NAME_TOPIC = "media_album_name_topic"
CONF_MEDIA_ARTIST_TEMPLATE = "media_artist_template"
CONF_MEDIA_ARTIST_TOPIC = "media_artist_topic"
CONF_MEDIA_CHANNEL_TEMPLATE = "media_channel_template"
CONF_MEDIA_CHANNEL_TOPIC = "media_channel_topic"
CONF_MEDIA_CONTENT_ID_TEMPLATE = "media_content_id_template"
CONF_MEDIA_CONTENT_ID_TOPIC = "media_content_id_topic"
CONF_MEDIA_CONTENT_TYPE_TEMPLATE = "media_content_type_template"
CONF_MEDIA_CONTENT_TYPE_TOPIC = "media_content_type_topic"
CONF_MEDIA_DURATION_TEMPLATE = "media_duration_template"
CONF_MEDIA_DURATION_TOPIC = "media_duration_topic"
CONF_MEDIA_EPISODE_TEMPLATE = "media_episode_template"
CONF_MEDIA_EPISODE_TOPIC = "media_episode_topic"
CONF_MEDIA_IMAGE_REMOTELY_ACCESSIBLE = "media_image_remotely_accessible"
CONF_MEDIA_IMAGE_URL_TEMPLATE = "media_image_url_template"
CONF_MEDIA_IMAGE_URL_TOPIC = "media_image_url_topic"
CONF_MEDIA_PLAYLIST_TEMPLATE = "media_playlist_template"
CONF_MEDIA_PLAYLIST_TOPIC = "media_playlist_topic"
CONF_MEDIA_POSITION_TEMPLATE = "media_position_template"
CONF_MEDIA_POSITION_TOPIC = "media_position_topic"
CONF_MEDIA_POSITION_UPDATED_AT_TEMPLATE = "media_position_updated_at_template"
CONF_MEDIA_POSITION_UPDATED_AT_TOPIC = "media_position_updated_at_topic"
CONF_MEDIA_SEASON_TEMPLATE = "media_season_template"
CONF_MEDIA_SEASON_TOPIC = "media_season_topic"
CONF_MEDIA_SERIES_TITLE_TEMPLATE = "media_series_title_template"
CONF_MEDIA_SERIES_TITLE_TOPIC = "media_series_title_topic"
CONF_MEDIA_TITLE_TEMPLATE = "media_title_template"
CONF_MEDIA_TITLE_TOPIC = "media_title_topic"
CONF_MEDIA_TRACK_TEMPLATE = "media_track_template"
CONF_MEDIA_TRACK_TOPIC = "media_track_topic"
CONF_SEND_IF_OFF = "send_if_off"
CONF_SHUFFLE_TEMPLATE = "shuffle_template"
CONF_SHUFFLE_TOPIC = "shuffle_topic"
CONF_SOUND_MODE_LIST = "sound_mode_list"
CONF_SOUND_MODE_STATE_TEMPLATE = "sound_mode_state_template"
CONF_SOUND_MODE_STATE_TOPIC = "sound_mode_state_topic"
CONF_SOURCE_LIST = "source_list"
CONF_SOURCE_STATE_TEMPLATE = "source_state_template"
CONF_SOURCE_STATE_TOPIC = "source_state_topic"
CONF_STATE_TEMPLATE = "state_template"
CONF_STATE_TOPIC = "state_topic"
CONF_VOLUME_LEVEL_TEMPLATE = "volume_level_template"
CONF_VOLUME_LEVEL_TOPIC = "volume_level_topic"
CONF_VOLUME_MAX = "max_volume"
CONF_VOLUME_MIN = "min_volume"
CONF_VOLUME_MUTE_STATE_TEMPLATE = "volume_mute_state_template"
CONF_VOLUME_MUTE_STATE_TOPIC = "volume_mute_state_topic"

CONF_CLEAR_PLAYLIST_COMMAND_TOPIC = "clear_playlist_command_topic"
CONF_NEXT_TRACK_COMMAND_TOPIC = "next_track_command_topic"
CONF_PAUSE_COMMAND_TOPIC = "pause_command_topic"
CONF_PLAY_COMMAND_TOPIC = "play_command_topic"
CONF_PLAY_MEDIA_COMMAND_TOPIC = "play_media_command_topic"
CONF_PREVIOUS_TRACK_COMMAND_TOPIC = "previous_track_command_topic"
CONF_SEEK_COMMAND_TOPIC = "seek_command_topic"
CONF_SHUFFLE_COMMAND_TOPIC = "shuffle_command_topic"
CONF_SOUND_MODE_COMMAND_TOPIC = "sound_mode_command_topic"
CONF_SOURCE_COMMAND_TOPIC = "source_command_topic"
CONF_STOP_COMMAND_TOPIC = "stop_command_topic"
CONF_TURN_OFF_COMMAND_TOPIC = "turn_off_command_topic"
CONF_TURN_ON_COMMAND_TOPIC = "turn_on_command_topic"
CONF_VOLUME_LEVEL_COMMAND_TOPIC = "volume_level_command_topic"
CONF_VOLUME_MUTE_COMMAND_TOPIC = "volume_mute_command_topic"

DEFAULT_MAX_VOLUME = 100
DEFAULT_MIN_VOLUME = 0

TEMPLATE_KEYS = (
    CONF_APP_ID_TEMPLATE,
    CONF_APP_NAME_TEMPLATE,
    CONF_ENTITY_PICTURE_TEMPLATE,
    CONF_MEDIA_ALBUM_ARTIST_TEMPLATE,
    CONF_MEDIA_ALBUM_NAME_TEMPLATE,
    CONF_MEDIA_ARTIST_TEMPLATE,
    CONF_MEDIA_CHANNEL_TEMPLATE,
    CONF_MEDIA_CONTENT_ID_TEMPLATE,
    CONF_MEDIA_CONTENT_TYPE_TEMPLATE,
    CONF_MEDIA_DURATION_TEMPLATE,
    CONF_MEDIA_EPISODE_TEMPLATE,
    CONF_MEDIA_IMAGE_URL_TEMPLATE,
    CONF_MEDIA_PLAYLIST_TEMPLATE,
    CONF_MEDIA_POSITION_TEMPLATE,
    CONF_MEDIA_POSITION_UPDATED_AT_TEMPLATE,
    CONF_MEDIA_SEASON_TEMPLATE,
    CONF_MEDIA_SERIES_TITLE_TEMPLATE,
    CONF_MEDIA_TITLE_TEMPLATE,
    CONF_MEDIA_TRACK_TEMPLATE,
    CONF_SHUFFLE_TEMPLATE,
    CONF_SOUND_MODE_STATE_TEMPLATE,
    CONF_STATE_TEMPLATE,
    CONF_SOURCE_STATE_TEMPLATE,
    CONF_VOLUME_LEVEL_TEMPLATE,
    CONF_VOLUME_MUTE_STATE_TEMPLATE,
)

TOPIC_KEYS = (
    CONF_APP_ID_TOPIC,
    CONF_APP_NAME_TOPIC,
    CONF_CLEAR_PLAYLIST_COMMAND_TOPIC,
    CONF_ENTITY_PICTURE_TOPIC,
    CONF_MEDIA_ALBUM_ARTIST_TOPIC,
    CONF_MEDIA_ALBUM_NAME_TOPIC,
    CONF_MEDIA_ARTIST_TOPIC,
    CONF_MEDIA_CHANNEL_TOPIC,
    CONF_MEDIA_CONTENT_ID_TOPIC,
    CONF_MEDIA_CONTENT_TYPE_TOPIC,
    CONF_MEDIA_DURATION_TOPIC,
    CONF_MEDIA_EPISODE_TOPIC,
    CONF_MEDIA_IMAGE_URL_TOPIC,
    CONF_MEDIA_PLAYLIST_TOPIC,
    CONF_MEDIA_POSITION_TOPIC,
    CONF_MEDIA_POSITION_UPDATED_AT_TOPIC,
    CONF_MEDIA_SEASON_TOPIC,
    CONF_MEDIA_SERIES_TITLE_TOPIC,
    CONF_MEDIA_TITLE_TOPIC,
    CONF_MEDIA_TRACK_TOPIC,
    CONF_NEXT_TRACK_COMMAND_TOPIC,
    CONF_PAUSE_COMMAND_TOPIC,
    CONF_PLAY_COMMAND_TOPIC,
    CONF_PLAY_MEDIA_COMMAND_TOPIC,
    CONF_PREVIOUS_TRACK_COMMAND_TOPIC,
    CONF_SEEK_COMMAND_TOPIC,
    CONF_SHUFFLE_COMMAND_TOPIC,
    CONF_SHUFFLE_TOPIC,
    CONF_SOUND_MODE_COMMAND_TOPIC,
    CONF_SOUND_MODE_STATE_TOPIC,
    CONF_SOURCE_COMMAND_TOPIC,
    CONF_SOURCE_STATE_TOPIC,
    CONF_STATE_TOPIC,
    CONF_STOP_COMMAND_TOPIC,
    CONF_TURN_OFF_COMMAND_TOPIC,
    CONF_TURN_ON_COMMAND_TOPIC,
    CONF_VOLUME_LEVEL_COMMAND_TOPIC,
    CONF_VOLUME_LEVEL_TOPIC,
    CONF_VOLUME_MUTE_COMMAND_TOPIC,
    CONF_VOLUME_MUTE_STATE_TOPIC,
)

SUPPORTED_COMMANDS_MAP = {
    CONF_CLEAR_PLAYLIST_COMMAND_TOPIC: SUPPORT_CLEAR_PLAYLIST,
    CONF_NEXT_TRACK_COMMAND_TOPIC: SUPPORT_NEXT_TRACK,
    CONF_PAUSE_COMMAND_TOPIC: SUPPORT_PAUSE,
    CONF_PLAY_COMMAND_TOPIC: SUPPORT_PLAY,
    CONF_PLAY_MEDIA_COMMAND_TOPIC: SUPPORT_PLAY_MEDIA,
    CONF_PREVIOUS_TRACK_COMMAND_TOPIC: SUPPORT_PREVIOUS_TRACK,
    CONF_SEEK_COMMAND_TOPIC: SUPPORT_SEEK,
    CONF_SHUFFLE_COMMAND_TOPIC: SUPPORT_SHUFFLE_SET,
    CONF_SOUND_MODE_COMMAND_TOPIC: SUPPORT_SELECT_SOUND_MODE,
    CONF_SOURCE_COMMAND_TOPIC: SUPPORT_SELECT_SOURCE,
    CONF_STOP_COMMAND_TOPIC: SUPPORT_STOP,
    CONF_TURN_OFF_COMMAND_TOPIC: SUPPORT_TURN_OFF,
    CONF_TURN_ON_COMMAND_TOPIC: SUPPORT_TURN_ON,
    CONF_VOLUME_LEVEL_COMMAND_TOPIC: SUPPORT_VOLUME_SET,
    CONF_VOLUME_MUTE_COMMAND_TOPIC: SUPPORT_VOLUME_MUTE,
}

SCHEMA_BASE = MEDIA_PLAYER_PLATFORM_SCHEMA.extend(MQTT_BASE_PLATFORM_SCHEMA.schema)
PLATFORM_SCHEMA = (
    SCHEMA_BASE.extend(
        {
            vol.Optional(CONF_APP_ID_TEMPLATE): cv.template,
            vol.Optional(CONF_APP_ID_TOPIC): mqtt.valid_subscribe_topic,
            vol.Optional(CONF_APP_NAME_TEMPLATE): cv.template,
            vol.Optional(CONF_APP_NAME_TOPIC): mqtt.valid_subscribe_topic,
            vol.Optional(CONF_CLEAR_PLAYLIST_COMMAND_TOPIC): mqtt.valid_publish_topic,
            vol.Optional(CONF_DEVICE): mqtt.MQTT_ENTITY_DEVICE_INFO_SCHEMA,
            vol.Optional(CONF_ENTITY_PICTURE_TEMPLATE): cv.template,
            vol.Optional(CONF_ENTITY_PICTURE_TOPIC): mqtt.valid_subscribe_topic,
            vol.Optional(CONF_MEDIA_ALBUM_ARTIST_TEMPLATE): cv.template,
            vol.Optional(CONF_MEDIA_ALBUM_ARTIST_TOPIC): mqtt.valid_subscribe_topic,
            vol.Optional(CONF_MEDIA_ALBUM_NAME_TEMPLATE): cv.template,
            vol.Optional(CONF_MEDIA_ALBUM_NAME_TOPIC): mqtt.valid_subscribe_topic,
            vol.Optional(CONF_MEDIA_ARTIST_TEMPLATE): cv.template,
            vol.Optional(CONF_MEDIA_ARTIST_TOPIC): mqtt.valid_subscribe_topic,
            vol.Optional(CONF_MEDIA_CHANNEL_TEMPLATE): cv.template,
            vol.Optional(CONF_MEDIA_CHANNEL_TOPIC): mqtt.valid_subscribe_topic,
            vol.Optional(CONF_MEDIA_CONTENT_ID_TEMPLATE): cv.template,
            vol.Optional(CONF_MEDIA_CONTENT_ID_TOPIC): mqtt.valid_subscribe_topic,
            vol.Optional(CONF_MEDIA_CONTENT_TYPE_TEMPLATE): cv.template,
            vol.Optional(CONF_MEDIA_CONTENT_TYPE_TOPIC): mqtt.valid_subscribe_topic,
            vol.Optional(CONF_MEDIA_DURATION_TEMPLATE): cv.template,
            vol.Optional(CONF_MEDIA_DURATION_TOPIC): mqtt.valid_subscribe_topic,
            vol.Optional(CONF_MEDIA_EPISODE_TEMPLATE): cv.template,
            vol.Optional(CONF_MEDIA_EPISODE_TOPIC): mqtt.valid_subscribe_topic,
            vol.Optional(CONF_MEDIA_IMAGE_REMOTELY_ACCESSIBLE, default=False): bool,
            vol.Optional(CONF_MEDIA_IMAGE_URL_TEMPLATE): cv.template,
            vol.Optional(CONF_MEDIA_IMAGE_URL_TOPIC): mqtt.valid_subscribe_topic,
            vol.Optional(CONF_MEDIA_PLAYLIST_TEMPLATE): cv.template,
            vol.Optional(CONF_MEDIA_PLAYLIST_TOPIC): mqtt.valid_subscribe_topic,
            vol.Optional(CONF_MEDIA_POSITION_TEMPLATE): cv.template,
            vol.Optional(CONF_MEDIA_POSITION_TOPIC): mqtt.valid_subscribe_topic,
            vol.Optional(CONF_MEDIA_POSITION_UPDATED_AT_TEMPLATE): cv.template,
            vol.Optional(
                CONF_MEDIA_POSITION_UPDATED_AT_TOPIC
            ): mqtt.valid_subscribe_topic,
            vol.Optional(CONF_MEDIA_SEASON_TEMPLATE): cv.template,
            vol.Optional(CONF_MEDIA_SEASON_TOPIC): mqtt.valid_subscribe_topic,
            vol.Optional(CONF_MEDIA_SERIES_TITLE_TEMPLATE): cv.template,
            vol.Optional(CONF_MEDIA_SERIES_TITLE_TOPIC): mqtt.valid_subscribe_topic,
            vol.Optional(CONF_MEDIA_TITLE_TEMPLATE): cv.template,
            vol.Optional(CONF_MEDIA_TITLE_TOPIC): mqtt.valid_subscribe_topic,
            vol.Optional(CONF_MEDIA_TRACK_TEMPLATE): cv.template,
            vol.Optional(CONF_MEDIA_TRACK_TOPIC): mqtt.valid_subscribe_topic,
            vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
            vol.Optional(CONF_NEXT_TRACK_COMMAND_TOPIC): mqtt.valid_publish_topic,
            vol.Optional(CONF_PAUSE_COMMAND_TOPIC): mqtt.valid_publish_topic,
            vol.Optional(CONF_PLAY_COMMAND_TOPIC): mqtt.valid_publish_topic,
            vol.Optional(CONF_PLAY_MEDIA_COMMAND_TOPIC): mqtt.valid_publish_topic,
            vol.Optional(CONF_PREVIOUS_TRACK_COMMAND_TOPIC): mqtt.valid_publish_topic,
            vol.Optional(CONF_RETAIN, default=mqtt.DEFAULT_RETAIN): cv.boolean,
            vol.Optional(CONF_SEEK_COMMAND_TOPIC): mqtt.valid_publish_topic,
            vol.Optional(CONF_SHUFFLE_COMMAND_TOPIC): mqtt.valid_publish_topic,
            vol.Optional(CONF_SOUND_MODE_COMMAND_TOPIC): mqtt.valid_publish_topic,
            vol.Optional(CONF_SOURCE_COMMAND_TOPIC): mqtt.valid_publish_topic,
            vol.Optional(CONF_SEND_IF_OFF, default=True): cv.boolean,
            vol.Optional(CONF_SHUFFLE_TEMPLATE): cv.template,
            vol.Optional(CONF_SHUFFLE_TOPIC): mqtt.valid_subscribe_topic,
            vol.Optional(CONF_SOUND_MODE_LIST): cv.ensure_list,
            vol.Optional(CONF_SOUND_MODE_STATE_TEMPLATE): cv.template,
            vol.Optional(CONF_SOUND_MODE_STATE_TOPIC): mqtt.valid_subscribe_topic,
            vol.Optional(CONF_SOURCE_LIST): cv.ensure_list,
            vol.Optional(CONF_SOURCE_STATE_TEMPLATE): cv.template,
            vol.Optional(CONF_SOURCE_STATE_TOPIC): mqtt.valid_subscribe_topic,
            vol.Optional(CONF_STATE_TEMPLATE): cv.template,
            vol.Optional(CONF_STATE_TOPIC): mqtt.valid_subscribe_topic,
            vol.Optional(CONF_STOP_COMMAND_TOPIC): mqtt.valid_publish_topic,
            vol.Optional(CONF_TURN_OFF_COMMAND_TOPIC): mqtt.valid_publish_topic,
            vol.Optional(CONF_TURN_ON_COMMAND_TOPIC): mqtt.valid_publish_topic,
            vol.Optional(CONF_VOLUME_LEVEL_COMMAND_TOPIC): mqtt.valid_publish_topic,
            vol.Optional(CONF_VOLUME_LEVEL_TEMPLATE): cv.template,
            vol.Optional(CONF_VOLUME_LEVEL_TOPIC): mqtt.valid_subscribe_topic,
            vol.Optional(CONF_VOLUME_MUTE_COMMAND_TOPIC): mqtt.valid_publish_topic,
            vol.Optional(CONF_VOLUME_MUTE_STATE_TEMPLATE): cv.template,
            vol.Optional(CONF_VOLUME_MUTE_STATE_TOPIC): mqtt.valid_subscribe_topic,
            vol.Optional(CONF_VOLUME_MIN, default=DEFAULT_MIN_VOLUME): vol.Coerce(
                float
            ),
            vol.Optional(CONF_VOLUME_MAX, default=DEFAULT_MAX_VOLUME): vol.Coerce(
                float
            ),
            vol.Optional(CONF_UNIQUE_ID): cv.string,
            vol.Optional(CONF_VALUE_TEMPLATE): cv.template,
        }
    )
    .extend(mqtt.MQTT_AVAILABILITY_SCHEMA.schema)
    .extend(mqtt.MQTT_JSON_ATTRS_SCHEMA.schema)
)


async def async_setup_platform(
    hass: HomeAssistantType, config: ConfigType, async_add_entities, discovery_info=None
):
    """Set up MQTT media player device through configuration.yaml."""
    await _async_setup_entity(hass, config, async_add_entities)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up MQTT media player device dynamically through MQTT discovery."""

    async def async_discover(discovery_payload):
        """Discover and add a MQTT media player device."""
        discovery_data = discovery_payload.discovery_data
        try:
            config = PLATFORM_SCHEMA(discovery_payload)
            await _async_setup_entity(
                hass, config, async_add_entities, config_entry, discovery_data
            )
        except Exception:
            clear_discovery_hash(hass, discovery_data[ATTR_DISCOVERY_HASH])
            raise

    async_dispatcher_connect(
        hass, MQTT_DISCOVERY_NEW.format(MP_DOMAIN, "mqtt"), async_discover
    )


async def _async_setup_entity(
    hass, config, async_add_entities, config_entry=None, discovery_data=None
):
    """Set up the MQTT media player devices."""
    async_add_entities([MqttMediaPlayer(hass, config, config_entry, discovery_data)])


class MqttMediaPlayer(
    MqttAttributes,
    MqttAvailability,
    MqttDiscoveryUpdate,
    MqttEntityDeviceInfo,
    MediaPlayerEntity,
):
    """Representation of an MQTT media player device."""

    def __init__(self, hass, config, config_entry, discovery_data):
        """Initialize the media player device."""
        self._config = config
        self._unique_id = config.get(CONF_UNIQUE_ID)
        self._sub_state = None

        self.hass = hass
        self._app_id = None
        self._app_name = None
        self._entity_picture = None
        self._is_volume_muted = None
        self._media_album_artist = None
        self._media_album_name = None
        self._media_artist = None
        self._media_channel = None
        self._media_content_id = None
        self._media_content_type = None
        self._media_duration = None
        self._media_episode = None
        self._media_image_url = None
        self._media_playlist = None
        self._media_position = None
        self._media_position_updated_at = None
        self._media_season = None
        self._media_series_title = None
        self._media_title = None
        self._media_track = None
        self._shuffle = None
        self._sound_mode = None
        self._source = None
        self._state = None
        self._volume_level = None

        self._topic = None
        self._value_templates = None

        self._setup_from_config(config)

        device_config = config.get(CONF_DEVICE)

        MqttAttributes.__init__(self, config)
        MqttAvailability.__init__(self, config)
        MqttDiscoveryUpdate.__init__(self, discovery_data, self.discovery_update)
        MqttEntityDeviceInfo.__init__(self, device_config, config_entry)

    async def async_added_to_hass(self):
        """Handle being added to Home Assistant."""
        await super().async_added_to_hass()
        await self._subscribe_topics()

    async def discovery_update(self, discovery_payload):
        """Handle updated discovery message."""
        config = PLATFORM_SCHEMA(discovery_payload)
        self._config = config
        self._setup_from_config(config)
        await self.attributes_discovery_update(config)
        await self.availability_discovery_update(config)
        await self.device_info_discovery_update(config)
        await self._subscribe_topics()
        self.async_write_ha_state()

    def _setup_from_config(self, config):
        """(Re)Setup the entity."""
        self._topic = {key: config.get(key) for key in TOPIC_KEYS}

        if self._topic[CONF_STATE_TOPIC] is None:
            self._state = STATE_ON

        value_templates = {}
        for key in TEMPLATE_KEYS:
            value_templates[key] = lambda value: value
        if CONF_VALUE_TEMPLATE in config:
            value_template = config.get(CONF_VALUE_TEMPLATE)
            value_template.hass = self.hass
            value_templates = {
                key: value_template.async_render_with_possible_json_value
                for key in TEMPLATE_KEYS
            }
        for key in TEMPLATE_KEYS & config.keys():
            tpl = config[key]
            value_templates[key] = tpl.async_render_with_possible_json_value
            tpl.hass = self.hass
        self._value_templates = value_templates

    async def _subscribe_topics(self):
        """(Re)Subscribe to topics."""
        topics = {}
        qos = self._config[CONF_QOS]

        def add_subscription(topics, topic, msg_callback):
            if self._topic[topic] is not None:
                topics[topic] = {
                    "topic": self._topic[topic],
                    "msg_callback": msg_callback,
                    "qos": qos,
                }

        def render_template(msg, template_name):
            template = self._value_templates[template_name]
            return template(msg.payload)

        def handle_app_id_received(msg):
            """Handle receiving app_id via MQTT."""
            payload = render_template(msg, CONF_APP_ID_TEMPLATE)

            self._app_id = payload
            self.async_write_ha_state()

        add_subscription(topics, CONF_APP_ID_TOPIC, handle_app_id_received)

        @callback
        @log_messages(self.hass, self.entity_id)
        def handle_app_name_received(msg):
            """Handle receiving app_name via MQTT."""
            payload = render_template(msg, CONF_APP_NAME_TEMPLATE)

            self._app_name = payload
            self.async_write_ha_state()

        add_subscription(topics, CONF_APP_NAME_TOPIC, handle_app_name_received)

        @callback
        @log_messages(self.hass, self.entity_id)
        def handle_entity_picture_received(msg):
            """Handle receiving entity_picture via MQTT."""
            payload = render_template(msg, CONF_ENTITY_PICTURE_TEMPLATE)

            self._entity_picture = payload
            self.async_write_ha_state()

        add_subscription(
            topics, CONF_ENTITY_PICTURE_TOPIC, handle_entity_picture_received
        )

        @callback
        @log_messages(self.hass, self.entity_id)
        def handle_is_volume_muted_received(msg):
            """Handle receiving is_volume_muted via MQTT."""
            payload = render_template(msg, CONF_VOLUME_MUTE_STATE_TEMPLATE)

            self._is_volume_muted = payload
            self.async_write_ha_state()

        add_subscription(
            topics, CONF_VOLUME_MUTE_STATE_TOPIC, handle_is_volume_muted_received
        )

        @callback
        @log_messages(self.hass, self.entity_id)
        def handle_media_album_artist_received(msg):
            """Handle receiving media_album_artist via MQTT."""
            payload = render_template(msg, CONF_MEDIA_ALBUM_ARTIST_TEMPLATE)

            self._media_album_artist = payload
            self.async_write_ha_state()

        add_subscription(
            topics, CONF_MEDIA_ALBUM_ARTIST_TOPIC, handle_media_album_artist_received
        )

        @callback
        @log_messages(self.hass, self.entity_id)
        def handle_media_album_name_received(msg):
            """Handle receiving media_album_name via MQTT."""
            payload = render_template(msg, CONF_MEDIA_ALBUM_NAME_TEMPLATE)

            self._media_album_name = payload
            self.async_write_ha_state()

        add_subscription(
            topics, CONF_MEDIA_ALBUM_NAME_TOPIC, handle_media_album_name_received
        )

        @callback
        @log_messages(self.hass, self.entity_id)
        def handle_media_artist_received(msg):
            """Handle receiving media_artist via MQTT."""
            payload = render_template(msg, CONF_MEDIA_ARTIST_TEMPLATE)

            self._media_artist = payload
            self.async_write_ha_state()

        add_subscription(topics, CONF_MEDIA_ARTIST_TOPIC, handle_media_artist_received)

        @callback
        @log_messages(self.hass, self.entity_id)
        def handle_media_channel_received(msg):
            """Handle receiving media_channel via MQTT."""
            payload = render_template(msg, CONF_MEDIA_CHANNEL_TEMPLATE)

            self._media_channel = payload
            self.async_write_ha_state()

        add_subscription(
            topics, CONF_MEDIA_CHANNEL_TOPIC, handle_media_channel_received
        )

        @callback
        @log_messages(self.hass, self.entity_id)
        def handle_media_content_id_received(msg):
            """Handle receiving media_content_id via MQTT."""
            payload = render_template(msg, CONF_MEDIA_CONTENT_ID_TEMPLATE)

            self._media_content_id = payload
            self.async_write_ha_state()

        add_subscription(
            topics, CONF_MEDIA_CONTENT_ID_TOPIC, handle_media_content_id_received
        )

        @callback
        @log_messages(self.hass, self.entity_id)
        def handle_media_content_type_received(msg):
            """Handle receiving media_content_type via MQTT."""
            payload = render_template(msg, CONF_MEDIA_CONTENT_TYPE_TEMPLATE)

            self._media_content_type = payload
            self.async_write_ha_state()

        add_subscription(
            topics, CONF_MEDIA_CONTENT_TYPE_TOPIC, handle_media_content_type_received
        )

        @callback
        @log_messages(self.hass, self.entity_id)
        def handle_media_duration_received(msg):
            """Handle receiving media_duration via MQTT."""
            payload = render_template(msg, CONF_MEDIA_DURATION_TEMPLATE)

            self._media_duration = payload
            self.async_write_ha_state()

        add_subscription(
            topics, CONF_MEDIA_DURATION_TOPIC, handle_media_duration_received
        )

        @callback
        @log_messages(self.hass, self.entity_id)
        def handle_media_episode_received(msg):
            """Handle receiving media_episode via MQTT."""
            payload = render_template(msg, CONF_MEDIA_EPISODE_TEMPLATE)

            self._media_episode = payload
            self.async_write_ha_state()

        add_subscription(
            topics, CONF_MEDIA_EPISODE_TOPIC, handle_media_episode_received
        )

        @callback
        @log_messages(self.hass, self.entity_id)
        def handle_media_image_url_received(msg):
            """Handle receiving media_image_url via MQTT."""
            payload = render_template(msg, CONF_MEDIA_IMAGE_URL_TEMPLATE)

            self._media_image_url = payload
            self.async_write_ha_state()

        add_subscription(
            topics, CONF_MEDIA_IMAGE_URL_TOPIC, handle_media_image_url_received
        )

        @callback
        @log_messages(self.hass, self.entity_id)
        def handle_media_playlist_received(msg):
            """Handle receiving media_playlist via MQTT."""
            payload = render_template(msg, CONF_MEDIA_PLAYLIST_TEMPLATE)

            self._media_playlist = payload
            self.async_write_ha_state()

        add_subscription(
            topics, CONF_MEDIA_PLAYLIST_TOPIC, handle_media_playlist_received
        )

        @callback
        @log_messages(self.hass, self.entity_id)
        def handle_media_position_received(msg):
            """Handle receiving media_position via MQTT."""
            payload = render_template(msg, CONF_MEDIA_POSITION_TEMPLATE)

            self._media_position = payload
            self.async_write_ha_state()

        add_subscription(
            topics, CONF_MEDIA_POSITION_TOPIC, handle_media_position_received
        )

        @callback
        @log_messages(self.hass, self.entity_id)
        def handle_media_position_updated_at_received(msg):
            """Handle receiving media_position_updated_at via MQTT."""
            payload = render_template(msg, CONF_MEDIA_POSITION_UPDATED_AT_TEMPLATE)

            self._media_position_updated_at = payload
            self.async_write_ha_state()

        add_subscription(
            topics,
            CONF_MEDIA_POSITION_UPDATED_AT_TOPIC,
            handle_media_position_updated_at_received,
        )

        @callback
        @log_messages(self.hass, self.entity_id)
        def handle_media_season_received(msg):
            """Handle receiving media_season via MQTT."""
            payload = render_template(msg, CONF_MEDIA_SEASON_TEMPLATE)

            self._media_season = payload
            self.async_write_ha_state()

        add_subscription(topics, CONF_MEDIA_SEASON_TOPIC, handle_media_season_received)

        @callback
        @log_messages(self.hass, self.entity_id)
        def handle_media_series_title_received(msg):
            """Handle receiving media_series_title via MQTT."""
            payload = render_template(msg, CONF_MEDIA_SERIES_TITLE_TEMPLATE)

            self._media_series_title = payload
            self.async_write_ha_state()

        add_subscription(
            topics, CONF_MEDIA_SERIES_TITLE_TOPIC, handle_media_series_title_received
        )

        @callback
        @log_messages(self.hass, self.entity_id)
        def handle_media_title_received(msg):
            """Handle receiving media_title via MQTT."""
            payload = render_template(msg, CONF_MEDIA_TITLE_TEMPLATE)

            self._media_title = payload
            self.async_write_ha_state()

        add_subscription(topics, CONF_MEDIA_TITLE_TOPIC, handle_media_title_received)

        @callback
        @log_messages(self.hass, self.entity_id)
        def handle_media_track_received(msg):
            """Handle receiving media_track via MQTT."""
            payload = render_template(msg, CONF_MEDIA_TRACK_TEMPLATE)

            self._media_track = int(payload)
            self.async_write_ha_state()

        add_subscription(topics, CONF_MEDIA_TRACK_TOPIC, handle_media_track_received)

        @callback
        @log_messages(self.hass, self.entity_id)
        def handle_shuffle_received(msg):
            """Handle receiving shuffle via MQTT."""
            payload = render_template(msg, CONF_SHUFFLE_TEMPLATE)

            self._shuffle = payload
            self.async_write_ha_state()

        add_subscription(topics, CONF_SHUFFLE_TOPIC, handle_shuffle_received)

        @callback
        def handle_item_in_list_received(msg, template_name, attr, list_name):
            """Handle receiving listed item via MQTT."""
            payload = render_template(msg, template_name)

            if self._config.get(list_name) and payload not in self._config[list_name]:
                _LOGGER.error("Invalid %s mode: %s", list_name, payload)
            else:
                setattr(self, attr, payload)
                self.async_write_ha_state()

        @callback
        @log_messages(self.hass, self.entity_id)
        def handle_sound_mode_state_received(msg):
            """Handle receiving sound_mode_state via MQTT."""
            handle_item_in_list_received(
                msg, CONF_SOUND_MODE_STATE_TEMPLATE, "_sound_mode", CONF_SOUND_MODE_LIST
            )

        add_subscription(
            topics, CONF_SOUND_MODE_STATE_TOPIC, handle_sound_mode_state_received
        )

        @callback
        @log_messages(self.hass, self.entity_id)
        def handle_source_state_received(msg):
            """Handle receiving source_state via MQTT."""
            handle_item_in_list_received(
                msg, CONF_SOURCE_STATE_TOPIC, "_source", CONF_SOURCE_LIST
            )

        add_subscription(topics, CONF_SOURCE_STATE_TOPIC, handle_source_state_received)

        @callback
        @log_messages(self.hass, self.entity_id)
        def handle_state_received(msg):
            """Handle receiving state via MQTT."""
            payload = render_template(msg, CONF_STATE_TEMPLATE)

            self._state = payload
            self.async_write_ha_state()

        add_subscription(topics, CONF_STATE_TOPIC, handle_state_received)

        @callback
        @log_messages(self.hass, self.entity_id)
        def handle_volume_level_received(msg):
            """Handle receiving volume_level via MQTT."""
            payload = render_template(msg, CONF_VOLUME_LEVEL_TEMPLATE)

            self._volume_level = float(payload)
            self.async_write_ha_state()

        add_subscription(topics, CONF_VOLUME_LEVEL_TOPIC, handle_volume_level_received)

        self._sub_state = await subscription.async_subscribe_topics(
            self.hass, self._sub_state, topics
        )

    async def async_will_remove_from_hass(self):
        """Unsubscribe when removed."""
        self._sub_state = await subscription.async_unsubscribe_topics(
            self.hass, self._sub_state
        )
        await MqttAttributes.async_will_remove_from_hass(self)
        await MqttAvailability.async_will_remove_from_hass(self)
        await MqttDiscoveryUpdate.async_will_remove_from_hass(self)

    @property
    def should_poll(self):
        """Return the polling state."""
        return False

    @property
    def name(self):
        """Return the name of the media player device."""
        return self._config[CONF_NAME]

    @property
    def unique_id(self):
        """Return a unique ID."""
        return self._unique_id

    @property
    def state(self):
        """State of the player."""
        return self._state

    @property
    def volume_level(self):
        """Volume level of the media player (0..1)."""
        if self._volume_level:
            return float(self._volume_level - self._config[CONF_VOLUME_MIN]) / (
                self._config[CONF_VOLUME_MAX] - self._config[CONF_VOLUME_MIN]
            )
        return None

    @property
    def is_volume_muted(self):
        """Boolean if volume is currently muted."""
        if self._is_volume_muted is not None:
            return self._is_volume_muted
        elif self.volume_level is not None:
            return self.volume_level == 0
        return None

    @property
    def media_content_id(self):
        """Content ID of current playing media."""
        return self._media_content_id

    @property
    def media_content_type(self):
        """Content type of current playing media."""
        return self._media_content_type

    @property
    def media_duration(self):
        """Duration of current playing media in seconds."""
        return self._media_duration

    @property
    def media_position(self):
        """Position of current playing media in seconds."""
        return self._media_position

    @property
    def media_position_updated_at(self):
        """When was the position of the current playing media valid.

        Returns value from homeassistant.util.dt.utcnow().
        """
        return self._media_position_updated_at

    @property
    def media_image_url(self):
        """Image url of current playing media."""
        return self._media_image_url

    @property
    def media_image_remotely_accessible(self) -> bool:
        """If the image url is remotely accessible."""
        return self._config[CONF_MEDIA_IMAGE_REMOTELY_ACCESSIBLE]

    @property
    def media_title(self):
        """Title of current playing media."""
        return self._media_title

    @property
    def media_artist(self):
        """Artist of current playing media, music track only."""
        return self._media_artist

    @property
    def media_album_name(self):
        """Album name of current playing media, music track only."""
        return self._media_album_name

    @property
    def media_album_artist(self):
        """Album artist of current playing media, music track only."""
        return self._media_album_artist

    @property
    def media_track(self):
        """Track number of current playing media, music track only."""
        return self._media_track

    @property
    def media_series_title(self):
        """Title of series of current playing media, TV show only."""
        return self._media_series_title

    @property
    def media_season(self):
        """Season of current playing media, TV show only."""
        return self._media_season

    @property
    def media_episode(self):
        """Episode of current playing media, TV show only."""
        return self._media_episode

    @property
    def media_channel(self):
        """Channel currently playing."""
        return self._media_channel

    @property
    def media_playlist(self):
        """Title of Playlist currently playing."""
        return self._media_playlist

    @property
    def app_id(self):
        """ID of the current running app."""
        return self._app_id

    @property
    def app_name(self):
        """Name of the current running app."""
        return self._app_name

    @property
    def source(self):
        """Name of the current input source."""
        return self._source

    @property
    def source_list(self):
        """List of available input sources."""
        return self._config.get(CONF_SOURCE_LIST)

    @property
    def sound_mode(self):
        """Name of the current sound mode."""
        return self._sound_mode

    @property
    def sound_mode_list(self):
        """List of available sound modes."""
        return self._config.get(CONF_SOUND_MODE_LIST)

    @property
    def shuffle(self):
        """Boolean if shuffle is enabled."""
        return self._shuffle

    def _publish(self, topic, payload="", attr=None, value=None):
        """Publish payload to topic if topic was provided."""
        assert self.hass
        if self._topic[topic] is not None and (
            self._config[CONF_SEND_IF_OFF] or self._state != STATE_OFF
        ):
            mqtt.async_publish(
                self.hass,
                self._topic[topic],
                payload,
                self._config[CONF_QOS],
                self._config[CONF_RETAIN],
            )
            if attr:
                value = value if value is not None else payload
                setattr(self, attr, value)
                self.async_write_ha_state()

    async def async_turn_on(self):
        """Turn the media player on."""
        self._publish(CONF_TURN_ON_COMMAND_TOPIC, STATE_ON, "_state")

    async def async_turn_off(self):
        """Turn the media player off."""
        self._publish(CONF_TURN_OFF_COMMAND_TOPIC, STATE_OFF, "_state")
        self._state = STATE_OFF
        self.async_write_ha_state()

    async def async_mute_volume(self, mute):
        """Mute the volume."""
        self._publish(CONF_VOLUME_MUTE_COMMAND_TOPIC, mute, "_is_volume_muted")

    async def async_set_volume_level(self, volume):
        """Set volume level, range 0..1."""
        self._publish(
            CONF_VOLUME_LEVEL_COMMAND_TOPIC,
            self._config[CONF_VOLUME_MIN]
            + (
                volume * (self._config[CONF_VOLUME_MAX] - self._config[CONF_VOLUME_MIN])
            ),
            "_volume_level",
            volume,
        )

    async def async_media_play(self):
        """Send play command."""
        self._publish(CONF_PLAY_COMMAND_TOPIC, "", "_state", STATE_PLAYING)

    async def async_media_pause(self):
        """Send pause command."""
        self._publish(CONF_PAUSE_COMMAND_TOPIC, "", "_state", STATE_ON)

    async def async_media_stop(self):
        """Send stop command."""
        self._publish(CONF_PAUSE_COMMAND_TOPIC, "", "_state", STATE_ON)

    async def async_media_previous_track(self):
        """Send previous track command."""
        self._publish(CONF_PREVIOUS_TRACK_COMMAND_TOPIC)

    async def async_media_next_track(self):
        """Send next track command."""
        self._publish(CONF_NEXT_TRACK_COMMAND_TOPIC)

    async def async_media_seek(self, position):
        """Send seek command."""
        self._publish(CONF_SEEK_COMMAND_TOPIC, position)

    async def async_play_media(self, media_type, media_id, **kwargs):
        """Play a piece of media."""
        data = kwargs or {}
        data["media_type"] = media_type
        data["media_id"] = media_id
        self._publish(CONF_PLAY_MEDIA_COMMAND_TOPIC, data)

    async def async_select_source(self, source):
        """Select input source."""
        if self.source_list and source in self.source_list:
            self._publish(CONF_SOURCE_COMMAND_TOPIC, source, "_source")

    async def async_select_sound_mode(self, sound_mode):
        """Select sound mode."""
        if self.sound_mode_list and sound_mode in self.sound_mode_list:
            self._publish(CONF_SOUND_MODE_COMMAND_TOPIC, sound_mode, "_sound_mode")

    async def async_clear_playlist(self):
        """Clear players playlist."""
        self._publish(CONF_CLEAR_PLAYLIST_COMMAND_TOPIC)

    async def async_set_shuffle(self, shuffle):
        """Enable/disable shuffle mode."""
        self._publish(CONF_SHUFFLE_COMMAND_TOPIC, shuffle, "_shuffle")

    @property
    def supported_features(self):
        """Return the list of supported features."""
        support = 0

        for topic in SUPPORTED_COMMANDS_MAP:
            if self._topic[topic] is not None:
                support |= SUPPORTED_COMMANDS_MAP[topic]

        return support
