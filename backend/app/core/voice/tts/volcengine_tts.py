"""
火山引擎 TTS 服务实现

支持两种 API 模式：
1. 实时对话 API (realtime_dialogue)：支持 4 种 jupiter_bigtts 音色
2. 普通 TTS bidirection API (bidirection_tts)：支持 BV_xxx_streaming 等更多音色

遵循设计原则：
- 配置外置：配置通过 VoiceConfig 或构造函数注入
- 全异步 I/O：所有 WebSocket 操作使用 async/await
- 错误标准化：返回 TTSChunk 和 TTSErrorCode

协议参考：
- 实时对话: https://www.volcengine.com/docs/6561/1329505
- 普通 TTS: https://www.volcengine.com/docs/6561/1329505
"""

import asyncio
import gzip
import json
import logging
import uuid
from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus

from .base import BaseTTSService, TTSChunk, TTSErrorCode
from ..config import get_voice_config, TTSConfig, TTSApiType

logger = logging.getLogger(__name__)


def _extract_status_code(exc: Exception) -> Optional[int]:
    """Normalize websocket handshake status extraction across versions."""
    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        return status_code

    response = getattr(exc, "response", None)
    if response is None:
        return None

    return getattr(response, "status_code", None) or getattr(response, "status", None)


# ========== 协议常量 ==========
PROTOCOL_VERSION = 0b0001
HEADER_SIZE = 0b0001

# Message Types
CLIENT_FULL_REQUEST = 0b0001
CLIENT_AUDIO_ONLY_REQUEST = 0b0010
SERVER_FULL_RESPONSE = 0b1001
SERVER_ACK = 0b1011
SERVER_ERROR_RESPONSE = 0b1111

# Message Type Specific Flags
NO_SEQUENCE = 0b0000
POS_SEQUENCE = 0b0001
NEG_SEQUENCE = 0b0010
MSG_WITH_EVENT = 0b0100
MSG_POSITIVE_ACK = 0b0001
MSG_NEGATIVE_ACK = 0b0010

# Serialization
JSON_SERIAL = 0b0001
NO_SERIALIZATION = 0b0000

# Compression
GZIP_COMPRESSION = 0b0001
NO_COMPRESSION = 0b0000

# Event IDs
EVENT_START_CONNECTION = 1
EVENT_FINISH_CONNECTION = 2
EVENT_CONNECTION_STARTED = 50
EVENT_CONNECTION_FAILED = 51
EVENT_START_SESSION = 100
EVENT_FINISH_SESSION = 102
EVENT_SESSION_STARTED = 150
EVENT_SESSION_FINISHED = 152
EVENT_TASK_REQUEST = 200  # 普通 TTS API 使用
EVENT_HELLO = 300         # 实时对话 API 使用
EVENT_TTS_SENTENCE_START = 350
EVENT_TTS_SENTENCE_END = 351
EVENT_TTS_RESPONSE = 352
EVENT_TTS_ENDED = 359


def _generate_header(
    version: int = PROTOCOL_VERSION,
    message_type: int = CLIENT_FULL_REQUEST,
    message_type_specific_flags: int = MSG_WITH_EVENT,
    serial_method: int = JSON_SERIAL,
    compression_type: int = GZIP_COMPRESSION,
    reserved_data: int = 0x00,
    extension_header: bytes = b""
) -> bytearray:
    """生成二进制协议头"""
    header = bytearray()
    header_size = int(len(extension_header) / 4) + 1
    header.append((version << 4) | header_size)
    header.append((message_type << 4) | message_type_specific_flags)
    header.append((serial_method << 4) | compression_type)
    header.append(reserved_data)
    header.extend(extension_header)
    return header


def _parse_response(data: bytes) -> dict:
    """解析服务端响应"""
    if isinstance(data, str) or len(data) < 4:
        return {}
    
    # Parse header
    header_size = data[0] & 0x0F
    message_type = data[1] >> 4
    message_type_specific_flags = data[1] & 0x0F
    serialization_method = data[2] >> 4
    message_compression = data[2] & 0x0F
    
    payload = data[header_size * 4:]
    result = {}
    payload_msg = None
    start = 0
    
    if message_type == SERVER_FULL_RESPONSE or message_type == SERVER_ACK:
        result['message_type'] = 'SERVER_FULL_RESPONSE'
        if message_type == SERVER_ACK:
            result['message_type'] = 'SERVER_ACK'
        
        # 检查 sequence
        if message_type_specific_flags & NEG_SEQUENCE > 0:
            if len(payload) >= 4:
                result['seq'] = int.from_bytes(payload[:4], "big", signed=False)
                start += 4
        
        # 检查 event
        if message_type_specific_flags & MSG_WITH_EVENT > 0:
            if len(payload) >= start + 4:
                result['event'] = int.from_bytes(payload[start:start + 4], "big", signed=False)
                start += 4
        
        payload = payload[start:]
        
        # Session ID
        if len(payload) >= 4:
            session_id_size = int.from_bytes(payload[:4], "big", signed=True)
            if session_id_size > 0 and len(payload) >= 4 + session_id_size:
                result['session_id'] = payload[4:4 + session_id_size].decode("utf-8", errors="ignore")
            payload = payload[4 + session_id_size:]
        
        # Payload
        if len(payload) >= 4:
            payload_msg = payload[4:]
        
    elif message_type == SERVER_ERROR_RESPONSE:
        result['message_type'] = 'SERVER_ERROR'
        if len(payload) >= 4:
            result['code'] = int.from_bytes(payload[:4], "big", signed=False)
        if len(payload) >= 8:
            payload_msg = payload[8:]
        result['is_error'] = True
    
    # 处理 payload_msg
    if payload_msg is not None:
        # 解压
        if message_compression == GZIP_COMPRESSION:
            try:
                payload_msg = gzip.decompress(payload_msg)
            except Exception:
                pass
        
        # 反序列化
        if serialization_method == JSON_SERIAL:
            try:
                payload_msg = json.loads(payload_msg.decode("utf-8"))
            except Exception:
                pass
        elif serialization_method != NO_SERIALIZATION:
            try:
                payload_msg = payload_msg.decode("utf-8")
            except Exception:
                pass
        
        result['payload_msg'] = payload_msg
        
        # 如果是 bytes，说明是音频数据
        if isinstance(payload_msg, bytes):
            result['audio_data'] = payload_msg
    
    return result


# ========== TTS 连接基类 ==========

class BaseTTSConnection(ABC):
    """
    TTS 连接基类
    
    定义公共接口，子类实现不同 API 协议。
    """
    
    def __init__(self, speaker: str, config: Optional[TTSConfig] = None):
        self.speaker = speaker
        self._config = config or get_voice_config().tts
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._session_id: Optional[str] = None
        self._connect_id: Optional[str] = None
        self._is_connected = False
        self._synth_lock = asyncio.Lock()
        self._is_synthesizing = False
        self._active_speaking_style: Optional[str] = None
    
    def is_alive(self) -> bool:
        """检查连接是否存活"""
        if not self._is_connected or self._ws is None:
            return False
        try:
            from websockets.protocol import State
            return self._ws.state == State.OPEN
        except (ImportError, AttributeError):
            return not getattr(self._ws, 'closed', True)
    
    async def connect(self, speaking_style: Optional[str] = None) -> bool:
        """建立连接并初始化 Session"""
        normalized_style = (speaking_style or "").strip() or None
        if self.is_alive() and self._is_connected and self._active_speaking_style == normalized_style:
            return True
        if self.is_alive():
            await self.close()
        
        try:
            self._connect_id = str(uuid.uuid4())
            self._session_id = str(uuid.uuid4())
            self._active_speaking_style = normalized_style

            api_app_key = (self._config.volcengine_app_key or "").strip()
            api_app_id = (self._config.volcengine_app_id or "").strip()
            api_access_key = (self._config.volcengine_access_key or "").strip()
            resource_id = (self._config.volcengine_resource_id or "").strip()
            ws_url = (self._config.volcengine_ws_url or "").strip()

            requires_realtime_app_key = not self._config.is_bidirection_tts()
            if (
                not api_app_id
                or not api_access_key
                or not resource_id
                or (requires_realtime_app_key and not api_app_key)
            ):
                logger.error(
                    "[TTSConnection] 配置缺失: requires_app_key=%s has_app_key=%s has_app_id=%s has_access_key=%s has_resource_id=%s",
                    requires_realtime_app_key,
                    bool(api_app_key),
                    bool(api_app_id),
                    bool(api_access_key),
                    bool(resource_id),
                )
                self._is_connected = False
                return False

            if self._config.is_bidirection_tts():
                headers = {
                    "X-Api-App-Key": api_app_id,
                    "X-Api-Access-Key": api_access_key,
                    "X-Api-Resource-Id": resource_id,
                    "X-Api-Connect-Id": self._connect_id,
                }
            else:
                headers = {
                    "X-Api-App-ID": api_app_id,
                    "X-Api-Access-Key": api_access_key,
                    "X-Api-Resource-Id": resource_id,
                    "X-Api-App-Key": api_app_key,
                    "X-Api-Connect-Id": self._connect_id,
                }
            
            logger.info(
                "[TTSConnection] 正在建立连接: url=%s, resource_id=%s, app_id=%s, api_type=%s",
                ws_url,
                resource_id,
                api_app_id,
                self._config.api_type
            )
            self._ws = await websockets.connect(
                ws_url,
                additional_headers=headers,
                ping_interval=None
            )
            
            # StartConnection
            await self._send_start_connection()
            
            # StartSession (子类实现)
            await self._send_start_session(speaking_style=normalized_style)
            
            self._is_connected = True
            logger.debug(
                "[TTSConnection] 连接已建立: session=%s, speaker=%s, speaking_style=%s",
                self._session_id[:8], 
                self.speaker,
                normalized_style or "default",
            )
            return True
            
        except (InvalidStatus, websockets.exceptions.InvalidStatusCode) as e:
            status_code = _extract_status_code(e)
            if status_code == 401:
                logger.error(
                    "[TTSConnection] 连接失败: 鉴权失败 (401 Unauthorized). url=%s resource_id=%s error=%s",
                    self._config.volcengine_ws_url,
                    self._config.volcengine_resource_id,
                    e,
                )
            else:
                logger.error(
                    "[TTSConnection] 连接失败: HTTP 状态码错误 %s. url=%s resource_id=%s error=%s",
                    status_code,
                    self._config.volcengine_ws_url,
                    self._config.volcengine_resource_id,
                    e,
                )
            self._is_connected = False
            return False
        except Exception as e:
            logger.error(
                "[TTSConnection] 连接失败: url=%s resource_id=%s error=%s",
                self._config.volcengine_ws_url,
                self._config.volcengine_resource_id,
                e,
            )
            self._is_connected = False
            return False
    
    async def _send_start_connection(self) -> None:
        """发送 StartConnection"""
        request = bytearray(_generate_header())
        request.extend(int(EVENT_START_CONNECTION).to_bytes(4, "big"))
        payload_bytes = gzip.compress(b"{}")
        request.extend(len(payload_bytes).to_bytes(4, "big"))
        request.extend(payload_bytes)
        await self._ws.send(request)
        try:
            response = await asyncio.wait_for(self._ws.recv(), timeout=5.0)
            parsed = _parse_response(response)
            if parsed.get('event') == EVENT_CONNECTION_FAILED:
                logger.error("[TTSConnection] ConnectionFailed: %s", parsed.get("payload_msg"))
                raise Exception("Connection failed")
        except asyncio.TimeoutError:
            logger.error("[TTSConnection] StartConnection handshake timeout")
            raise Exception("StartConnection timeout")
    
    @abstractmethod
    async def _send_start_session(self, speaking_style: Optional[str] = None) -> None:
        """发送 StartSession（子类实现）"""
        pass
    
    @abstractmethod
    async def synthesize_streaming(
        self, 
        text: str,
        speaking_style: Optional[str] = None,
        max_retries: int = 2
    ) -> AsyncIterator[bytes]:
        """流式合成文本（子类实现）"""
        pass
    
    async def close(self) -> None:
        """关闭连接"""
        if self._ws:
            try:
                payload_bytes = gzip.compress(b"{}")
                request = bytearray(_generate_header())
                request.extend(int(EVENT_FINISH_SESSION).to_bytes(4, "big"))
                request.extend(len(self._session_id).to_bytes(4, "big"))
                request.extend(self._session_id.encode("utf-8"))
                request.extend(len(payload_bytes).to_bytes(4, "big"))
                request.extend(payload_bytes)
                await self._ws.send(request)
            except Exception:
                pass
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
            self._is_connected = False
            self._active_speaking_style = None


# ========== 实时对话 API 连接 ==========

class RealtimeDialogueConnection(BaseTTSConnection):
    """
    实时对话 API 连接
    
    协议特点：
    - 使用 EVENT_HELLO (300) 发送文本
    - Session 结构: {tts: {...}, dialog: {...}}
    - 支持音色: zh_female_vv_jupiter_bigtts 等 4 种
    """
    
    async def _send_start_session(self, speaking_style: Optional[str] = None) -> None:
        """发送 StartSession"""
        session_config = {
            "tts": {
                "speaker": self.speaker,
                "audio_config": {
                    "channel": 1,
                    "format": self._config.output_format,
                    "sample_rate": self._config.sample_rate
                }
            },
            "dialog": {
                "extra": {
                    "input_mod": "text",
                    "recv_timeout": 60
                }
            }
        }
        normalized_style = (speaking_style or "").strip()
        if normalized_style:
            session_config["dialog"]["speaking_style"] = normalized_style
        
        payload_bytes = gzip.compress(json.dumps(session_config).encode("utf-8"))
        request = bytearray(_generate_header())
        request.extend(int(EVENT_START_SESSION).to_bytes(4, "big"))
        request.extend(len(self._session_id).to_bytes(4, "big"))
        request.extend(self._session_id.encode("utf-8"))
        request.extend(len(payload_bytes).to_bytes(4, "big"))
        request.extend(payload_bytes)
        
        await self._ws.send(request)
        try:
            await asyncio.wait_for(self._ws.recv(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.error("[TTSConnection] StartSession handshake timeout")
            raise Exception("StartSession timeout")
    
    async def synthesize_streaming(
        self, 
        text: str,
        speaking_style: Optional[str] = None,
        max_retries: int = 2
    ) -> AsyncIterator[bytes]:
        """流式合成文本（实时对话 API 协议）"""
        if self._is_synthesizing:
            logger.info("[TTSConnection] 检测到旧合成仍在进行，强制关闭连接")
            await self.close()
        
        async with self._synth_lock:
            self._is_synthesizing = True
            try:
                for attempt in range(max_retries + 1):
                    if not await self.connect(speaking_style=speaking_style):
                        if attempt < max_retries:
                            await asyncio.sleep(0.3 * (attempt + 1))
                            continue
                        return
                
                    try:
                        logger.debug("[TTSConnection] HELLO: text=%s", text[:50] if len(text) > 50 else text)
                        payload = {"content": text}
                        payload_bytes = gzip.compress(json.dumps(payload).encode("utf-8"))
                        request = bytearray(_generate_header())
                        request.extend(int(EVENT_HELLO).to_bytes(4, "big"))
                        request.extend(len(self._session_id).to_bytes(4, "big"))
                        request.extend(self._session_id.encode("utf-8"))
                        request.extend(len(payload_bytes).to_bytes(4, "big"))
                        request.extend(payload_bytes)
                        await self._ws.send(request)
                        
                        tts_ended = False
                        while not tts_ended:
                            try:
                                response = await asyncio.wait_for(self._ws.recv(), timeout=self._config.timeout)
                                parsed = _parse_response(response)
                                
                                msg_type = parsed.get("message_type")
                                event = parsed.get("event")
                                
                                if msg_type == 'SERVER_ERROR':
                                    logger.error("[TTSConnection] 服务器错误: %s", parsed.get("payload_msg"))
                                    break
                                
                                payload = parsed.get("payload_msg")
                                if msg_type == 'SERVER_ACK' and isinstance(payload, bytes):
                                    yield payload
                                
                                if event == EVENT_TTS_ENDED:
                                    logger.debug("[TTSConnection] TTS 合成完成标志 (359)")
                                    self._is_connected = False
                                    self._active_speaking_style = None
                                    tts_ended = True
                                    break
                                    
                            except asyncio.TimeoutError:
                                logger.warning("[TTSConnection] 接收超时")
                                break
                            except ConnectionClosed:
                                logger.warning("[TTSConnection] 连接已关闭")
                                self._is_connected = False
                                break
                        
                        if tts_ended:
                            break
                            
                    except ConnectionClosed:
                        self._is_connected = False
                        if attempt < max_retries:
                            await asyncio.sleep(0.3 * (attempt + 1))
                            continue
                    except Exception as e:
                        logger.error("[TTSConnection] 合成异常: %s", e)
                        if attempt < max_retries:
                            await asyncio.sleep(0.3 * (attempt + 1))
                            continue
                        raise
            finally:
                self._is_synthesizing = False


# ========== 普通 TTS API 连接 ==========

class BidirectionTTSConnection(BaseTTSConnection):
    """
    普通 TTS bidirection API 连接
    
    协议特点：
    - 使用 EVENT_TASK_REQUEST (200) 发送文本
    - Session 结构: {req_params: {text, speaker, audio_params}}
    - 使用 JSON 无压缩格式（与 realtime_dialogue API 不同）
    """
    
    async def _send_start_connection(self) -> None:
        """发送 StartConnection（普通 TTS API 使用 JSON 无压缩）"""
        request = bytearray(_generate_header(compression_type=NO_COMPRESSION))
        request.extend(int(EVENT_START_CONNECTION).to_bytes(4, "big"))
        payload_bytes = b"{}"
        request.extend(len(payload_bytes).to_bytes(4, "big"))
        request.extend(payload_bytes)
        await self._ws.send(request)
        try:
            response = await asyncio.wait_for(self._ws.recv(), timeout=5.0)
            parsed = _parse_response(response)
            if parsed.get('event') == EVENT_CONNECTION_FAILED:
                logger.error("[BidirectionTTS] ConnectionFailed: %s", parsed.get("payload_msg"))
                raise Exception("Connection failed")
            else:
                logger.debug("[BidirectionTTS] ConnectionStarted")
        except asyncio.TimeoutError:
            logger.error("[BidirectionTTS] StartConnection handshake timeout")
            raise Exception("StartConnection timeout")
    
    async def _send_start_session(self, speaking_style: Optional[str] = None) -> None:
        """发送 StartSession（不含文本，只设置参数）
        
        注意：普通 TTS bidirection API 使用 JSON 无压缩格式
        """
        output_format = self._get_bidirection_output_format()
        session_config = {
            "user": {
                "uid": self._config.volcengine_uid or "default_uid"
            },
            "req_params": {
                "speaker": self.speaker,
                "audio_params": {
                    "format": output_format,
                    "sample_rate": self._config.sample_rate
                }
            }
        }
        
        # 添加情感参数（如果支持）
        normalized_style = (speaking_style or "").strip()
        if normalized_style:
            session_config["req_params"]["audio_params"]["emotion"] = "friendly"
        
        # 普通 TTS API: JSON 无压缩
        payload_bytes = json.dumps(session_config).encode("utf-8")
        request = bytearray(_generate_header(compression_type=NO_COMPRESSION))
        request.extend(int(EVENT_START_SESSION).to_bytes(4, "big"))
        request.extend(len(self._session_id).to_bytes(4, "big"))
        request.extend(self._session_id.encode("utf-8"))
        request.extend(len(payload_bytes).to_bytes(4, "big"))
        request.extend(payload_bytes)
        
        await self._ws.send(request)
        try:
            response = await asyncio.wait_for(self._ws.recv(), timeout=5.0)
            parsed = _parse_response(response)
            event = parsed.get('event')
            if event == EVENT_SESSION_STARTED:
                logger.debug("[TTSConnection] SessionStarted")
            else:
                logger.warning("[TTSConnection] Unexpected session response: event=%s", event)
        except asyncio.TimeoutError:
            logger.error("[TTSConnection] StartSession handshake timeout")
            raise Exception("StartSession timeout")

    def _get_bidirection_output_format(self) -> str:
        """火山双向 TTS 文档只支持 mp3 / ogg_opus / pcm。"""
        output_format = (self._config.output_format or "").strip() or "pcm"
        if output_format == "pcm_s16le":
            logger.info("[TTSConnection] bidirection 输出格式从 pcm_s16le 规范化为 pcm")
            return "pcm"
        return output_format

    async def _send_finish_session(self) -> None:
        """普通 TTS 在文本发送结束后要立即显式结束 session。"""
        payload_bytes = b"{}"
        request = bytearray(_generate_header(compression_type=NO_COMPRESSION))
        request.extend(int(EVENT_FINISH_SESSION).to_bytes(4, "big"))
        request.extend(len(self._session_id).to_bytes(4, "big"))
        request.extend(self._session_id.encode("utf-8"))
        request.extend(len(payload_bytes).to_bytes(4, "big"))
        request.extend(payload_bytes)
        await self._ws.send(request)
    
    async def synthesize_streaming(
        self, 
        text: str,
        speaking_style: Optional[str] = None,
        max_retries: int = 2
    ) -> AsyncIterator[bytes]:
        """流式合成文本（普通 TTS API 协议）"""
        if self._is_synthesizing:
            logger.info("[TTSConnection] 检测到旧合成仍在进行，强制关闭连接")
            await self.close()
        
        async with self._synth_lock:
            self._is_synthesizing = True
            try:
                for attempt in range(max_retries + 1):
                    if not await self.connect(speaking_style=speaking_style):
                        if attempt < max_retries:
                            await asyncio.sleep(0.3 * (attempt + 1))
                            continue
                        return
                
                    try:
                        # 使用 TaskRequest 发送文本
                        # 注意：普通 TTS bidirection API 使用 JSON 无压缩格式
                        logger.debug("[TTSConnection] TaskRequest: text=%s", text[:50] if len(text) > 50 else text)
                        output_format = self._get_bidirection_output_format()
                        task_payload = {
                            "req_params": {
                                "text": text,
                                "speaker": self.speaker,
                                "audio_params": {
                                    "format": output_format,
                                    "sample_rate": self._config.sample_rate
                                }
                            }
                        }
                        # 普通 TTS API: JSON 无压缩
                        payload_bytes = json.dumps(task_payload).encode("utf-8")
                        request = bytearray(_generate_header(compression_type=NO_COMPRESSION))
                        request.extend(int(EVENT_TASK_REQUEST).to_bytes(4, "big"))
                        request.extend(len(self._session_id).to_bytes(4, "big"))
                        request.extend(self._session_id.encode("utf-8"))
                        request.extend(len(payload_bytes).to_bytes(4, "big"))
                        request.extend(payload_bytes)
                        await self._ws.send(request)
                        await self._send_finish_session()
                        
                        audio_received = False
                        session_finished = False
                        timed_out = False
                        while not session_finished:
                            try:
                                response = await asyncio.wait_for(self._ws.recv(), timeout=self._config.timeout)
                                parsed = _parse_response(response)
                                
                                msg_type = parsed.get("message_type")
                                event = parsed.get("event")
                                
                                if msg_type == 'SERVER_ERROR':
                                    logger.error("[TTSConnection] 服务器错误: %s", parsed.get("payload_msg"))
                                    break
                                
                                # 音频数据
                                payload = parsed.get("payload_msg")
                                if msg_type == 'SERVER_ACK' and isinstance(payload, bytes):
                                    audio_received = True
                                    yield payload
                                
                                # Session 结束
                                if event == EVENT_SESSION_FINISHED:
                                    logger.debug("[TTSConnection] SessionFinished")
                                    session_finished = True
                                    break
                                    
                            except asyncio.TimeoutError:
                                timed_out = True
                                logger.warning(
                                    "[TTSConnection] 接收超时: session_finished=%s audio_received=%s",
                                    session_finished,
                                    audio_received,
                                )
                                break
                            except ConnectionClosed:
                                logger.warning("[TTSConnection] 连接已关闭")
                                self._is_connected = False
                                break
                        
                        if session_finished:
                            self._is_connected = False
                            self._active_speaking_style = None
                            break

                        if timed_out:
                            await self.close()
                            if audio_received:
                                logger.warning(
                                    "[TTSConnection] 已收到音频但未等到 SessionFinished，停止重试以避免重复合成"
                                )
                                break
                            if attempt < max_retries:
                                await asyncio.sleep(0.3 * (attempt + 1))
                                continue
                            
                    except ConnectionClosed:
                        self._is_connected = False
                        if attempt < max_retries:
                            await asyncio.sleep(0.3 * (attempt + 1))
                            continue
                    except Exception as e:
                        logger.error("[TTSConnection] 合成异常: %s", e)
                        if attempt < max_retries:
                            await asyncio.sleep(0.3 * (attempt + 1))
                            continue
                        raise
            finally:
                self._is_synthesizing = False
    
    async def close(self) -> None:
        """关闭连接（普通 TTS API 使用 JSON 无压缩）"""
        if self._ws:
            try:
                await self._send_finish_session()
            except Exception:
                pass
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
            self._is_connected = False


# ========== TTS 连接工厂 ==========

def create_tts_connection(speaker: str, config: Optional[TTSConfig] = None) -> BaseTTSConnection:
    """
    根据配置创建对应的 TTS 连接实例
    
    Args:
        speaker: 音色 ID
        config: TTS 配置
        
    Returns:
        BaseTTSConnection 实例
    """
    tts_config = config or get_voice_config().tts
    
    if tts_config.is_bidirection_tts():
        logger.debug("[TTSFactory] 创建 BidirectionTTSConnection, speaker=%s", speaker)
        return BidirectionTTSConnection(speaker, tts_config)
    else:
        logger.debug("[TTSFactory] 创建 RealtimeDialogueConnection, speaker=%s", speaker)
        return RealtimeDialogueConnection(speaker, tts_config)


# ========== TTS 连接池 ==========

class TTSConnectionPool:
    """
    TTS 连接池
    
    为每个会话维护一个 TTS 长连接，实现连接复用。
    """
    
    def __init__(self, config: Optional[TTSConfig] = None):
        self._config = config or get_voice_config().tts
        self._connections: dict[str, BaseTTSConnection] = {}
        self._lock = asyncio.Lock()
    
    async def get_connection(self, session_id: str, speaker: str) -> BaseTTSConnection:
        """获取或创建连接"""
        async with self._lock:
            if session_id in self._connections:
                conn = self._connections[session_id]
                if conn.is_alive() and conn.speaker == speaker:
                    return conn
                else:
                    await conn.close()
            
            conn = create_tts_connection(speaker, self._config)
            self._connections[session_id] = conn
            return conn
    
    async def release_connection(self, session_id: str) -> None:
        """释放连接"""
        async with self._lock:
            if session_id in self._connections:
                conn = self._connections.pop(session_id)
                await conn.close()
    
    async def close_all(self) -> None:
        """关闭所有连接"""
        async with self._lock:
            for conn in self._connections.values():
                await conn.close()
            self._connections.clear()


# ========== TTS 服务类 ==========

class VolcengineTTSService(BaseTTSService):
    """
    火山引擎 TTS 服务
    
    实现 BaseTTSService 抽象接口，支持：
    - 双 API 模式（实时对话 / 普通 TTS）
    - 音色切换 (voice_id)
    - 语速调整 (speed)  
    - 流式输出
    """
    
    def __init__(self, config: Optional[TTSConfig] = None):
        """
        初始化 TTS 服务
        
        Args:
            config: TTS 配置。若为 None，则使用全局配置。
        """
        self._config = config or get_voice_config().tts
        self._pool = TTSConnectionPool(self._config)
    
    def resolve_voice_id(self, voice_key: str) -> str:
        """
        解析音色 key 为实际的音色 ID
        
        支持的 key: "ghost", "guide", "child", "robot"
        
        Args:
            voice_key: 音色 key
            
        Returns:
            实际的音色 ID
        """
        # 根据 API 类型选择映射表
        voice_map = self._config.get_voice_map()
        if voice_key in voice_map:
            return voice_map[voice_key]
        
        # 如果已经是完整的音色 ID，直接返回
        return voice_key
    
    def get_available_voices(self) -> list[dict]:
        """获取可用音色列表"""
        if self._config.is_bidirection_tts():
            return [
                {"id": "BV050_streaming", "name": "动漫小新", "description": "卡通风格"},
                {"id": "BV001_streaming", "name": "通用男声", "description": "标准男声"},
                {"id": "BV002_streaming", "name": "通用女声", "description": "标准女声"},
            ]
        else:
            return [
                {"id": "zh_female_vv_jupiter_bigtts", "name": "VV女声", "description": "温柔女声"},
                {"id": "zh_female_xiaohe_jupiter_bigtts", "name": "晓禾女声", "description": "亲切女声"},
                {"id": "zh_male_yunzhou_jupiter_bigtts", "name": "云洲男声", "description": "沉稳男声"},
                {"id": "zh_male_xiaotian_jupiter_bigtts", "name": "小天男声", "description": "活力男声"},
            ]
    
    async def synthesize(
        self, 
        text: str,
        voice_id: Optional[str] = None,
        speed: float = 1.0,
        speaking_style: Optional[str] = None,
        **kwargs
    ) -> bytes:
        """
        一次性语音合成
        
        收集所有流式数据后返回完整音频
        """
        audio_chunks = []
        async for chunk in self.synthesize_streaming(
            text, voice_id, speed, speaking_style, **kwargs
        ):
            if not chunk.is_error and chunk.audio_data:
                audio_chunks.append(chunk.audio_data)
        return b"".join(audio_chunks)
    
    async def synthesize_streaming(
        self, 
        text: str,
        voice_id: Optional[str] = None,
        speed: float = 1.0,
        speaking_style: Optional[str] = None,
        session_id: Optional[str] = None,
        **kwargs
    ) -> AsyncIterator[TTSChunk]:
        """
        流式语音合成
        
        Args:
            text: 待合成文本
            voice_id: 音色 ID（如 "ghost", "guide"）
            speed: 语速 (0.5 - 2.0)
            speaking_style: 情感风格
            session_id: 会话 ID（用于连接池复用）
            
        Yields:
            TTSChunk: 音频数据块
        """
        # 检查文本
        if not text or not text.strip():
            yield TTSChunk.error(TTSErrorCode.EMPTY_TEXT, "文本为空")
            return
        
        if len(text) > 5000:
            yield TTSChunk.error(TTSErrorCode.TEXT_TOO_LONG, "文本过长")
            return
        
        # 解析音色
        actual_voice_id = self.resolve_voice_id(voice_id or "guide")
        
        # 获取连接
        conn_session_id = session_id or str(uuid.uuid4())
        
        try:
            conn = await self._pool.get_connection(conn_session_id, actual_voice_id)
            
            # 流式合成
            async for audio_data in conn.synthesize_streaming(
                text, 
                speaking_style=speaking_style
            ):
                yield TTSChunk.data(
                    audio_data, 
                    text=text,
                    sample_rate=self._config.sample_rate
                )
            
            # 发送结束标记
            yield TTSChunk.end()
            
        except ConnectionClosed:
            yield TTSChunk.error(TTSErrorCode.CONNECTION_CLOSED, "连接断开")
        except Exception as e:
            logger.error("[VolcengineTTS] 合成失败: %s", e, exc_info=True)
            yield TTSChunk.error(TTSErrorCode.UNKNOWN_ERROR, str(e))
        finally:
            # 如果是临时会话，释放连接
            if not session_id:
                await self._pool.release_connection(conn_session_id)
    
    async def release_session(self, session_id: str) -> None:
        """释放会话连接"""
        await self._pool.release_connection(session_id)
    
    async def close_all(self) -> None:
        """关闭所有连接"""
        await self._pool.close_all()


# ========== 连接池单例 ==========

_tts_connection_pool: Optional[TTSConnectionPool] = None


def get_tts_connection_pool() -> TTSConnectionPool:
    """获取 TTS 连接池单例"""
    global _tts_connection_pool
    if _tts_connection_pool is None:
        _tts_connection_pool = TTSConnectionPool()
    return _tts_connection_pool


# ========== 兼容别名 ==========

# 向后兼容：TTSConnection 是 BaseTTSConnection 的别名
TTSConnection = BaseTTSConnection
