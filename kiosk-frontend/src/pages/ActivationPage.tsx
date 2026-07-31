import { useState, type FormEvent } from "react";
import { Gift, KeyRound, Loader2, Mic, ShieldCheck } from "lucide-react";

import { client } from "../api/client";
import { setDeviceToken } from "../api/config";
import { useSessionStore } from "../store/session";

function mapActivationError(error: unknown): string {
  const message = error instanceof Error ? error.message : "激活失败";
  if (message.includes("activation_key_invalid")) {
    return "激活码无效，请输入管理端生成的完整激活码。";
  }
  if (message.includes("activation_key_used")) {
    return "该激活码已经被使用，请重新生成新的激活码。";
  }
  if (message.includes("activation_key_revoked")) {
    return "该激活码已失效，请联系管理员重新发放。";
  }
  return message;
}

const READY_MODULES = [
  {
    icon: ShieldCheck,
    title: "设备绑定",
    description: "一次激活后自动绑定当前大屏设备。",
  },
  {
    icon: Mic,
    title: "语音问答",
    description: "游客可直接语音提问，现场获得讲解回答。",
  },
  {
    icon: Gift,
    title: "答题领奖",
    description: "完成互动挑战后可扫码领取奖励。",
  },
];

export default function ActivationPage() {
  const setPage = useSessionStore((state) => state.setPage);
  const [activationKey, setActivationKey] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!activationKey.trim() || loading) return;

    setLoading(true);
    setError("");
    try {
      const result = await client.activateDevice({ activation_key: activationKey.trim() });
      setDeviceToken(result.device_token);
      setPage("Attract");
    } catch (err) {
      setError(mapActivationError(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="relative z-10 flex h-full w-full items-center px-10 py-10 text-white">
      <div className="grid w-full gap-8 lg:grid-cols-[1.08fr_0.92fr]">
        <section className="flex flex-col justify-between">
          <div>
            <div className="kiosk-chip">Device Activation</div>
            <h1 className="kiosk-display kiosk-glow-text mt-8 text-5xl font-black leading-tight xl:text-7xl">
              Delu Science Kiosk
            </h1>
            <p className="mt-8 max-w-[46rem] text-2xl leading-relaxed text-white/74 xl:text-[2rem]">
              先完成设备激活，再进入导览、导购、知识问答与互动答题一体化体验。整台设备只需激活一次。
            </p>
          </div>

          <div className="mt-10 grid gap-4 md:grid-cols-3">
            {READY_MODULES.map(({ icon: Icon, title, description }) => (
              <div key={title} className="kiosk-panel p-6">
                <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-cyan-300/16 bg-cyan-300/8">
                  <Icon className="h-7 w-7 text-cyan-100" />
                </div>
                <div className="mt-5 text-2xl font-bold text-white">{title}</div>
                <p className="mt-3 text-base leading-7 text-white/60">{description}</p>
              </div>
            ))}
          </div>
        </section>

        <form onSubmit={handleSubmit} className="kiosk-panel p-10 xl:p-12">
          <div className="flex items-center gap-4">
            <div className="flex h-16 w-16 items-center justify-center rounded-2xl border border-white/10 bg-white/6">
              <KeyRound className="h-8 w-8 text-cyan-100" />
            </div>
            <div>
              <div className="kiosk-chip kiosk-chip--warm">Secure Binding</div>
              <h2 className="kiosk-heading mt-4 text-4xl font-black text-white">输入激活码</h2>
            </div>
          </div>

          <p className="mt-8 text-lg leading-8 text-white/65">
            激活成功后，系统会将当前设备与工作区绑定。后续重启设备时会自动进入互动首页。
          </p>

          <label htmlFor="activation-code" className="mt-10 block text-sm font-semibold uppercase tracking-[0.28em] text-white/52">
            Activation Key
          </label>
          <input
            id="activation-code"
            value={activationKey}
            onChange={(event) => setActivationKey(event.target.value)}
            className="mt-4 h-20 w-full rounded-[1.4rem] border border-white/12 bg-black/30 px-6 text-2xl tracking-[0.24em] text-white outline-none transition-colors placeholder:text-white/25 focus:border-cyan-300/45"
            placeholder="请输入激活码"
            autoComplete="off"
            disabled={loading}
          />

          <div className="mt-4 flex items-center gap-3 text-sm text-white/46">
            <span className="h-2 w-2 rounded-full bg-cyan-300" />
            激活码为一次性凭证，请从管理端复制完整内容。
          </div>

          {error ? (
            <div className="mt-6 rounded-2xl border border-rose-300/22 bg-rose-400/10 px-5 py-4 text-base text-rose-100">
              {error}
            </div>
          ) : null}

          <button
            type="submit"
            disabled={loading || !activationKey.trim()}
            className="kiosk-button mt-10 w-full justify-center text-xl"
          >
            {loading ? <Loader2 className="h-6 w-6 animate-spin" /> : null}
            {loading ? "激活中..." : "激活并进入互动首页"}
          </button>
        </form>
      </div>
    </div>
  );
}
