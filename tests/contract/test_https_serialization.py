"""Optional local PHP/WP-CLI engine check; no WordPress bootstrap, DB or network."""

import json
import shutil
import subprocess

import pytest

from wp_modernizer.domain.https import internal_http_pattern


def test_real_wpcli_engine_preserves_php_serialization(tmp_path):
    php, wp = shutil.which("php"), shutil.which("wp")
    if not php or not wp:
        pytest.skip("PHP and a WP-CLI phar are required for the local serialization contract")
    phar = tmp_path / "wp.phar"
    shutil.copyfile(wp, phar)
    script = tmp_path / "verify.php"
    script.write_text(r"""<?php
$source = 'phar://' . $argv[1]
    . '/vendor/wp-cli/search-replace-command/src/WP_CLI/SearchReplacer.php';
if (!file_exists($source)) { exit(77); }
require $source;
$input = [
    'widget' => ['url' => 'http://teste.example.org/widget'],
    'html' => '<img src="http://teste.example.org/image">',
    'css' => 'background-image:url(http://teste.example.org/bg)',
    'nested' => serialize(['url' => 'http://teste.example.org/nested']),
    'external' => 'http://teste.example.org.external/path',
    'external_service' => 'http://external-service.example',
];
$replacer = new WP_CLI\SearchReplacer(
    $argv[2], 'https://teste.example.org', true, true, 'i', chr(1)
);
$serialized = serialize($input);
$result = $replacer->run($serialized);
$decoded = unserialize($result);
$decoded['nested'] = unserialize($decoded['nested']);
echo json_encode(['decoded' => $decoded, 'serialized' => $result,
                  'idempotent' => $replacer->run($result) === $result]);
""")
    result = subprocess.run(  # noqa: S603
        [php, str(script), str(phar), internal_http_pattern("teste.example.org")],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode == 77:
        pytest.skip("Installed WP-CLI is not a phar containing SearchReplacer")
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    payload = json.loads(result.stdout)
    assert payload["idempotent"]
    decoded = payload["decoded"]
    assert decoded["widget"]["url"] == "https://teste.example.org/widget"
    assert decoded["nested"]["url"] == "https://teste.example.org/nested"
    assert decoded["html"] == '<img src="https://teste.example.org/image">'
    assert decoded["css"] == "background-image:url(https://teste.example.org/bg)"
    assert decoded["external"] == "http://teste.example.org.external/path"
    assert decoded["external_service"] == "http://external-service.example"
    assert 's:32:"https://teste.example.org/widget"' in payload["serialized"]
