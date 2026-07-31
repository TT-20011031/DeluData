"""
阿里云 ASR 服务实现

使用阿里云一句话识别 RESTful API
API文档: https://help.aliyun.com/document_detail/92131.html
支持格式: PCM, WAV, OGG(OPUS), SPEEX, AMR, MP3, AAC
采样率: 8000Hz, 16000Hz

遵循设计原则：
- 配置外置：配置通过构造函数注入或从 VoiceConfig 读取
- 全异步 I/O：所有网络请求使用 async/await
- 错误标准化：返回 ASRResult 和 ASRErrorCode
"""

import json
import time
import logging
import tempfile
import asyncio
import shutil
import os
import subprocess
from urllib.parse import urlencode
from typing import Optional, Dict, Any

import httpx

from .base import BaseASRService, ASRResult, ASRErrorCode
from ..config import get_voice_config, ASRConfig

logger = logging.getLogger(__name__)

# Token 缓存（模块级别）
_token_cache: Dict[str, Any] = {'token': None, 'expire_time': 0}


class AliyunASRService(BaseASRService):
    """
    阿里云语音识别服务
    
    支持依赖注入配置，也可使用默认配置（从环境变量读取）
    """
    
    def __init__(self, config: Optional[ASRConfig] = None):
        """
        初始化 ASR 服务
        
        Args:
            config: ASR 配置。若为 None，则使用全局配置。
        """
        self._config = config or get_voice_config().asr
        
        # 从配置中读取参数
        self._appkey = self._config.aliyun_appkey
        self._access_key_id = self._config.aliyun_access_key_id
        self._access_key_secret = self._config.aliyun_access_key_secret
        self._ffmpeg_path = self._config.ffmpeg_path
        self._sample_rate = self._config.sample_rate
        
        # 阿里云 NLS 配置（可通过环境变量覆盖）
        self._nls_host = os.getenv(
            "ALIYUN_NLS_HOST", 
            "nls-gateway-cn-shanghai.aliyuncs.com"
        )
        self._region = os.getenv("ALIYUN_REGION", "cn-shanghai")
        self._nls_meta_host = os.getenv(
            "ALIYUN_NLS_META_HOST", 
            "nls-meta.cn-shanghai.aliyuncs.com"
        )
        
        # 检查配置完整性
        if not all([self._appkey, self._access_key_id, self._access_key_secret]):
            logger.warning("[AliyunASR] 配置不完整，请设置环境变量")
    
    def get_appkey(self) -> Optional[str]:
        """获取 AppKey（用于前端直连）"""
        return self._appkey

    @staticmethod
    def _classify_provider_error(
        status: Optional[Any],
        message: str
    ) -> ASRErrorCode:
        """Map Aliyun NLS error payloads to user-facing ASR error classes."""
        status_text = str(status or "")
        error_text = f"{status_text} {message or ''}".lower()

        rate_limit_markers = (
            "qps",
            "rate limit",
            "too many requests",
            "throttl",
            "flow limit",
            "限流",
            "请求太频繁",
        )
        if any(marker in error_text for marker in rate_limit_markers):
            return ASRErrorCode.RATE_LIMIT

        auth_or_quota_markers = (
            "free_trial_expired",
            "free trial has expired",
            "quota",
            "insufficient",
            "balance",
            "arrears",
            "billing",
            "expired",
            "unauthorized",
            "forbidden",
            "invalid token",
            "invalid appkey",
            "appkey",
            "signature",
            "accesskey",
            "permission",
            "欠费",
            "额度",
            "试用",
            "过期",
            "认证",
            "权限",
        )
        if status_text == "40000010" or any(
            marker in error_text for marker in auth_or_quota_markers
        ):
            return ASRErrorCode.AUTH_ERROR

        return ASRErrorCode.SERVICE_ERROR
    
    async def get_token(self) -> Optional[str]:
        """
        获取阿里云 NLS Token（带缓存）
        
        Token 有效期为 24 小时，提前 1 小时刷新
        """
        global _token_cache
        current_time = time.time()
        
        # Token 缓存，提前 1 小时刷新
        if _token_cache['token'] and _token_cache['expire_time'] > current_time + 3600:
            return _token_cache['token']
        
        try:
            from aliyunsdkcore.client import AcsClient
            from aliyunsdkcore.request import CommonRequest
        except ImportError:
            logger.error("[AliyunASR] 缺少依赖: pip install aliyun-python-sdk-core")
            return None
        
        try:
            # 异步执行同步 SDK 调用
            def _create_token():
                client = AcsClient(
                    self._access_key_id, 
                    self._access_key_secret, 
                    self._region
                )
                request = CommonRequest()
                request.set_method('POST')
                request.set_domain(self._nls_meta_host)
                request.set_version('2019-02-28')
                request.set_action_name('CreateToken')
                return client.do_action_with_exception(request)
            
            response = await asyncio.to_thread(_create_token)
            result = json.loads(response)
            
            if 'Token' in result and 'Id' in result['Token']:
                _token_cache['token'] = result['Token']['Id']
                _token_cache['expire_time'] = result['Token'].get(
                    'ExpireTime', 
                    current_time + 86400
                )
                logger.info("[AliyunASR] Token 获取成功")
                return _token_cache['token']
            
            logger.error(f"[AliyunASR] Token 响应异常: {result}")
            return None
            
        except Exception as e:
            logger.error(f"[AliyunASR] Token 获取失败: {e}", exc_info=True)
            return None
    
    async def _convert_to_wav(
        self, 
        audio_content: bytes, 
        input_format: str = 'webm'
    ) -> Optional[bytes]:
        """
        使用 ffmpeg 将音频转换为 WAV 格式（异步）
        
        Args:
            audio_content: 原始音频数据
            input_format: 输入格式
            
        Returns:
            WAV 格式音频数据，失败返回 None
        """
        # 查找 ffmpeg
        ffmpeg = self._ffmpeg_path or shutil.which('ffmpeg')
        if not ffmpeg:
            # 跨平台路径检测
            candidate_paths = [
                # Linux/macOS
                '/usr/bin/ffmpeg',
                '/usr/local/bin/ffmpeg',
                # Windows 常见安装路径
                r'C:\ffmpeg\bin\ffmpeg.exe',
                r'C:\Program Files\ffmpeg\bin\ffmpeg.exe',
                r'C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe',
            ]
            for path in candidate_paths:
                if os.path.exists(path):
                    ffmpeg = path
                    break
        
        if not ffmpeg:
            import platform
            os_hint = "请添加 ffmpeg 到 PATH" if platform.system() == "Windows" else "请安装 ffmpeg (apt/brew install ffmpeg)"
            logger.error(f"[AliyunASR] 未找到 ffmpeg。{os_hint}")
            return None
        
        logger.debug(f"[AliyunASR] 使用 ffmpeg: {ffmpeg}")
        
        # 创建临时文件
        with tempfile.NamedTemporaryFile(
            suffix=f'.{input_format}', 
            delete=False
        ) as f:
            f.write(audio_content)
            input_path = f.name
        
        output_path = input_path + '.wav'
        
        try:
            # 转换命令：16kHz, 单声道, WAV 格式
            cmd = [
                ffmpeg, '-y', '-i', input_path, 
                '-ar', str(self._sample_rate), 
                '-ac', '1', 
                '-f', 'wav', 
                output_path
            ]
            
            def run_ffmpeg():
                return subprocess.run(cmd, capture_output=True, timeout=30)
            
            result = await asyncio.to_thread(run_ffmpeg)
            
            if result.returncode != 0:
                logger.error(f"[AliyunASR] 转换失败: {result.stderr.decode()}")
                return None
            
            with open(output_path, 'rb') as f:
                wav_data = f.read()
            
            logger.debug(f"[AliyunASR] 转换成功, WAV 大小: {len(wav_data)} bytes")
            return wav_data
            
        except Exception as e:
            logger.error(f"[AliyunASR] 格式转换异常: {e}", exc_info=True)
            return None
            
        finally:
            # 清理临时文件
            try:
                os.remove(input_path)
                if os.path.exists(output_path):
                    os.remove(output_path)
            except Exception:
                pass
    
    async def transcribe(
        self, 
        audio_content: bytes, 
        audio_format: str = 'wav',
        sample_rate: int = 16000
    ) -> ASRResult:
        """
        语音转文字
        
        Args:
            audio_content: 音频二进制数据
            audio_format: 音频格式 (wav/mp3/pcm)
            sample_rate: 采样率
            
        Returns:
            ASRResult: 识别结果
        """
        # 检查配置
        if not self._appkey:
            return ASRResult.fail(
                ASRErrorCode.CONFIG_ERROR, 
                "ASR 配置不完整"
            )
        
        # 获取 Token
        token = await self.get_token()
        if not token:
            return ASRResult.fail(
                ASRErrorCode.AUTH_ERROR, 
                "Token 获取失败"
            )
        
        # 构建请求参数
        params = {
            'appkey': self._appkey,
            'format': audio_format,
            'sample_rate': sample_rate,
            'enable_punctuation_prediction': 'true',
            'enable_inverse_text_normalization': 'true'
        }
        
        url = f"https://{self._nls_host}/stream/v1/asr?" + urlencode(params)
        headers = {
            'X-NLS-Token': token,
            'Content-type': 'application/octet-stream',
        }
        
        logger.info(f"[AliyunASR] 发送请求: format={audio_format}, size={len(audio_content)}")
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    url, 
                    content=audio_content, 
                    headers=headers
                )
            
            logger.debug(f"[AliyunASR] 响应状态: {response.status_code}")
            
            if response.status_code != 200:
                provider_status = None
                provider_message = response.text
                try:
                    error_payload = response.json()
                    provider_status = error_payload.get("status")
                    provider_message = error_payload.get("message") or response.text
                except Exception:
                    pass

                logger.error(
                    "[AliyunASR] HTTP 错误: status=%s, provider_status=%s, message=%s",
                    response.status_code,
                    provider_status,
                    provider_message,
                )
                return ASRResult.fail(
                    self._classify_provider_error(provider_status, provider_message),
                    f"HTTP {response.status_code}: {provider_message}"
                )
            
            result = response.json()
            logger.debug(f"[AliyunASR] 响应: {result}")
            
            # 阿里云 ASR 成功状态码
            if result.get('status') == 20000000:
                text = result.get('result', '')
                if not text:
                    return ASRResult.fail(
                        ASRErrorCode.EMPTY_AUDIO, 
                        "未检测到语音内容"
                    )
                return ASRResult.ok(text)
            
            # 处理阿里云错误码
            error_msg = result.get('message', '识别失败')
            error_code = self._classify_provider_error(
                result.get('status'),
                error_msg
            )
            return ASRResult.fail(error_code, error_msg)
            
        except httpx.TimeoutException:
            return ASRResult.fail(ASRErrorCode.NETWORK_ERROR, "请求超时")
        except httpx.NetworkError as e:
            return ASRResult.fail(ASRErrorCode.NETWORK_ERROR, str(e))
        except Exception as e:
            logger.error(f"[AliyunASR] 识别异常: {e}", exc_info=True)
            return ASRResult.fail(ASRErrorCode.UNKNOWN_ERROR, str(e))
    
    async def transcribe_file(
        self, 
        audio_content: bytes, 
        content_type: str = ""
    ) -> ASRResult:
        """
        处理音频文件并转文字（自动处理格式转换）
        
        Args:
            audio_content: 音频二进制数据
            content_type: MIME 类型 (如 audio/webm)
            
        Returns:
            ASRResult: 识别结果
        """
        # 检查音频大小
        if len(audio_content) == 0:
            return ASRResult.fail(ASRErrorCode.EMPTY_AUDIO, "音频为空")
        
        if len(audio_content) > 10 * 1024 * 1024:  # 10MB 限制
            return ASRResult.fail(ASRErrorCode.AUDIO_TOO_LARGE, "音频文件过大")
        
        try:
            # 根据 MIME 类型确定格式和是否需要转换
            content_type_lower = content_type.lower()
            
            if any(x in content_type_lower for x in ['webm', 'ogg', 'opus']):
                logger.info("[AliyunASR] 转换 WebM/OGG 到 WAV...")
                wav_data = await self._convert_to_wav(audio_content, 'webm')
                if wav_data is None:
                    return ASRResult.fail(
                        ASRErrorCode.FORMAT_ERROR, 
                        "音频格式转换失败"
                    )
                return await self.transcribe(wav_data, 'wav', self._sample_rate)
                
            elif 'wav' in content_type_lower:
                return await self.transcribe(
                    audio_content, 'wav', self._sample_rate
                )
                
            elif 'mp3' in content_type_lower:
                return await self.transcribe(
                    audio_content, 'mp3', self._sample_rate
                )
                
            else:
                # 未知格式，尝试转换
                logger.info("[AliyunASR] 未知格式，尝试转换...")
                wav_data = await self._convert_to_wav(audio_content, 'webm')
                if wav_data is None:
                    return ASRResult.fail(
                        ASRErrorCode.FORMAT_ERROR, 
                        "音频格式不支持"
                    )
                return await self.transcribe(wav_data, 'wav', self._sample_rate)
                
        except Exception as e:
            logger.error(f"[AliyunASR] 处理失败: {e}", exc_info=True)
            return ASRResult.fail(ASRErrorCode.UNKNOWN_ERROR, str(e))
