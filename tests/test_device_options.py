import pytest

from conpass.config import DeviceConfig
from conpass.device import DeviceError, build_options


def test_signing_caps():
    cfg = DeviceConfig(udid="00008140-000105E40AA2801C", bundle_id="jp.example.app",
                       xcode_org_id="ABCDE12345", updated_wda_bundle_id="com.me.WebDriverAgentRunner")
    caps = build_options(cfg).to_capabilities()
    assert caps["appium:xcodeOrgId"] == "ABCDE12345"
    assert caps["appium:xcodeSigningId"] == "Apple Development"
    assert caps["appium:updatedWDABundleId"] == "com.me.WebDriverAgentRunner"
    assert caps["appium:allowProvisioningDeviceRegistration"] is True
    assert caps["appium:noReset"] is True and caps["appium:autoAcceptAlerts"] is False
    assert "appium:xcodeConfigFile" not in caps


def test_provisioning_can_be_disabled_and_required_fields():
    cfg = DeviceConfig(udid="u", bundle_id="b", allow_provisioning_device_registration=False)
    assert "appium:allowProvisioningDeviceRegistration" not in build_options(cfg).to_capabilities()
    with pytest.raises(DeviceError):
        build_options(DeviceConfig())
