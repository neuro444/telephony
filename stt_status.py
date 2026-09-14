"""Non-secret runtime selection, read from the gateway process configuration."""
import config


def current_status():
    provider = config.STT_PROVIDER
    model = {
        'deepgram': getattr(config, 'DEEPGRAM_MODEL', None),
        'assemblyai': getattr(config, 'ASSEMBLY_MODEL', None),
        'sarvam': getattr(config, 'SARVAM_MODEL', None),
        'plivo': getattr(config, 'PLIVO_SPEECH_MODEL', None),
        'elevenlabs': 'scribe_v2_realtime',
        # The remote endpoint's loaded model cannot be verified from its URL.
        'whisper_manglish_hf': None,
    }.get(provider)
    language = {
        'deepgram': getattr(config, 'DEEPGRAM_LANGUAGE', config.SPEECH_LANGUAGE),
        'plivo': config.SPEECH_LANGUAGE,
        'sarvam': getattr(config, 'SARVAM_LANGUAGE', None),
        'elevenlabs': getattr(config, 'ELEVENLABS_STT_LANGUAGE', None),
    }.get(provider)
    return {
        'provider': provider,
        'model': model,
        'language': language,
        'carrier': config.current_carrier() if hasattr(config, 'current_carrier') else 'plivo',
        'scope': 'current_gateway_configuration',
        'live_transcription_verified': False,
    }
