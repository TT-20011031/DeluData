from app.museum.config import MuseumSettings


def test_museum_settings_reads_shared_tts_api_type_env(monkeypatch):
    monkeypatch.setenv("MUSEUM_TTS_API_TYPE", "bidirection_tts")
    monkeypatch.delenv("MUSEUM_VOLCANO_TTS_API_TYPE", raising=False)

    settings = MuseumSettings()

    assert settings.volcano_tts_api_type == "bidirection_tts"
    assert settings.get_voice_for_person("妇女") == "zh_female_peiqi_mars_bigtts"


def test_get_voice_for_person_reuses_core_kiosk_voice_mapping_for_realtime():
    settings = MuseumSettings(volcano_tts_api_type="realtime_dialogue")

    assert settings.get_voice_for_person("商务人士") == "zh_male_yunzhou_jupiter_bigtts"
    assert settings.get_voice_for_person("儿童") == "zh_female_xiaohe_jupiter_bigtts"
    assert settings.get_voice_for_person("妇女") == "zh_female_vv_jupiter_bigtts"
    assert settings.get_voice_for_person("老年人") == "zh_male_yunzhou_jupiter_bigtts"
    assert settings.get_voice_for_person("通用访客") == "zh_female_vv_jupiter_bigtts"
    assert settings.get_voice_for_person("未知类型") == "zh_female_vv_jupiter_bigtts"


def test_get_voice_for_person_reuses_core_kiosk_voice_mapping_for_bidirection():
    settings = MuseumSettings(volcano_tts_api_type="bidirection_tts")

    assert settings.get_voice_for_person("商务人士") == "zh_female_peiqi_mars_bigtts"
    assert settings.get_voice_for_person("儿童") == "zh_female_peiqi_mars_bigtts"
    assert settings.get_voice_for_person("妇女") == "zh_female_peiqi_mars_bigtts"
    assert settings.get_voice_for_person("老年人") == "zh_female_peiqi_mars_bigtts"
    assert settings.get_voice_for_person("通用访客") == "zh_female_peiqi_mars_bigtts"
    assert settings.get_voice_for_person("未知类型") == "zh_female_peiqi_mars_bigtts"
