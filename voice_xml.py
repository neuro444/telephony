"""Select the carrier XML dialect independently of speech recognition."""
import config
import plivo_xml
import twilio_xml


def __getattr__(name):
    return getattr(twilio_xml if config.current_carrier() == "twilio" else plivo_xml, name)
