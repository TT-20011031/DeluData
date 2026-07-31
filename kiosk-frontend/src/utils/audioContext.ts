/**
 * 模块级 AudioContext 单例管理
 *
 * 浏览器 Autoplay Policy 要求 AudioContext 必须在用户手势的同步调用栈内创建或 resume。
 * 将 AudioContext 提升为模块级单例，与 React 组件生命周期完全解耦：
 * - 首次用户手势时调用 unlockSharedAudioContext() 完成解锁
 * - 此后永远复用同一个实例，不关闭、不重建
 */

type AudioContextCtor = typeof AudioContext;

let _sharedCtx: AudioContext | null = null;

function createAudioContext(): AudioContext | null {
  try {
    const Ctor: AudioContextCtor | undefined =
      window.AudioContext ??
      (window as Window & { webkitAudioContext?: AudioContextCtor }).webkitAudioContext;
    if (!Ctor) {
      console.warn('[AudioContext] Web Audio API not supported in this browser.');
      return null;
    }
    return new Ctor();
  } catch (e) {
    console.warn('[AudioContext] Failed to create AudioContext:', e);
    return null;
  }
}

/**
 * 在用户手势的同步调用栈内调用，创建并解锁共享 AudioContext。
 * 幂等：已 running 时直接跳过；已 suspended 时触发 resume。
 * 注意：ctx.resume() 是异步的，但创建本身是同步的，浏览器会记录手势上下文。
 */
export function unlockSharedAudioContext(): void {
  try {
    if (!_sharedCtx || _sharedCtx.state === 'closed') {
      _sharedCtx = createAudioContext();
    }
    if (_sharedCtx && _sharedCtx.state === 'suspended') {
      void _sharedCtx.resume();
    }
  } catch (e) {
    console.warn('[AudioContext] unlock failed:', e);
  }
}

/**
 * 获取当前共享 AudioContext 单例。
 * 若尚未通过用户手势解锁，返回 null。
 */
export function getSharedAudioContext(): AudioContext | null {
  if (!_sharedCtx || _sharedCtx.state === 'closed') {
    return null;
  }
  return _sharedCtx;
}

/**
 * 快速判断共享 AudioContext 是否处于可用（running）状态。
 */
export function isAudioContextReady(): boolean {
  return _sharedCtx !== null && _sharedCtx.state === 'running';
}
