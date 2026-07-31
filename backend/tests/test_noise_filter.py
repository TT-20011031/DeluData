import sys

sys.path.insert(0, ".")

from app.core.rag.noise_filter import NoiseFilter


def test_noise_filter_detects_watermark_noise():
    detector = NoiseFilter(
        marker_density_threshold=0.01,
        min_readable_ratio=0.3,
        repeat_ratio_threshold=0.6,
    )
    text = "www.bzfxw.com www.bzfxw.com www.bzfxw.com ---- ----"
    decision = detector.evaluate(text)
    assert decision.is_noise is True


def test_noise_filter_keeps_normal_content():
    detector = NoiseFilter()
    text = "设备预测性维护通过传感器数据分析，提前发现故障并降低停机时间。"
    decision = detector.evaluate(text)
    assert decision.is_noise is False

