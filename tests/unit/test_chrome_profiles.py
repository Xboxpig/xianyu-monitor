import json
from pathlib import Path

import pytest

from src.infrastructure.desktop import chrome_profiles
from src.services import native_chrome_launcher


def make_profiles(tmp_path, monkeypatch):
    monkeypatch.setenv('CHROME_USER_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(chrome_profiles.sys, 'platform', 'win32')
    for directory in ('Default', 'Profile 1'):
        (tmp_path / directory).mkdir()
    (tmp_path / 'Local State').write_text(json.dumps({'profile': {'info_cache': {
        'Default': {'name': 'Personal', 'user_name': 'user@example.com'},
        'Profile 1': {'name': 'Work'}, 'Profile 2': {'name': 'Deleted'},
        '../outside': {'name': 'Unsafe'}, 'C:\\outside': {'name': 'Unsafe'},
    }}}), encoding='utf-8')


def test_detects_existing_profiles_and_rejects_arbitrary_paths(tmp_path, monkeypatch):
    make_profiles(tmp_path, monkeypatch)
    profiles = chrome_profiles.list_chrome_profiles()
    assert [p['directory'] for p in profiles] == ['Default', 'Profile 1']
    assert profiles[0]['name'] == 'Personal'
    assert profiles[1]['path'] == 'chrome-profile:Profile 1'
    for directory in ('../outside', 'Profile 2', '--remote-debugging-port=9222'):
        with pytest.raises(ValueError):
            chrome_profiles.require_chrome_profile(directory)


def test_profile_launch_uses_existing_directory(tmp_path, monkeypatch):
    make_profiles(tmp_path, monkeypatch)
    launched = []
    monkeypatch.setattr(native_chrome_launcher, 'find_chrome', lambda: Path('chrome.exe'))
    monkeypatch.setattr(native_chrome_launcher.subprocess, 'Popen', lambda args, **kwargs: launched.append(args))
    native_chrome_launcher.launch_chrome('https://www.goofish.com', profile_directory='Profile 1')
    assert '--profile-directory=Profile 1' in launched[0]
    assert '--user-data-dir=' + str(tmp_path.resolve()) in launched[0]
    with pytest.raises(ValueError):
        native_chrome_launcher.launch_chrome('https://www.goofish.com', profile_directory='Profile 2')
    assert len(launched) == 1


def test_unavailable_or_invalid_local_state_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv('CHROME_USER_DATA_DIR', str(tmp_path))
    assert chrome_profiles.list_chrome_profiles() == []
    (tmp_path / 'Local State').write_text('{broken', encoding='utf-8')
    assert chrome_profiles.list_chrome_profiles() == []
