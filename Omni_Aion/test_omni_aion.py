#!/usr/bin/env python3
"""
Тесты модуля Omni_Aion (шифрование, бандлы, установщик, верификация).
Запуск:
    python -m unittest Omni_Aion/test_omni_aion.py
"""

import sys
import unittest
from pathlib import Path

_CUR_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _CUR_DIR.parent if (_CUR_DIR.parent / "config_app.py").exists() else _CUR_DIR
if str(_PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(_PROJECT_DIR))
if str(_CUR_DIR) not in sys.path:
    sys.path.insert(0, str(_CUR_DIR))

import Omni_Aion.stack_bundle_manager as sbm
import Omni_Aion.verify_stack as vs


class TestOmniAionCrypto(unittest.TestCase):
    def test_key_fingerprint(self):
        fp1 = sbm.compute_key_fingerprint("SecretKey2026!")
        fp2 = sbm.compute_key_fingerprint("SecretKey2026!")
        fp_other = sbm.compute_key_fingerprint("OtherKey2026!")
        self.assertEqual(fp1, fp2)
        self.assertNotEqual(fp1, fp_other)

    def test_aes_gcm_roundtrip(self):
        key = "MasterKey999"
        raw = b"Sensitive tokens: refresh_token_xyz_12345"
        enc = sbm._encrypt_data(key, raw)
        self.assertTrue(enc.startswith(sbm.MAGIC_HEADER))
        dec = sbm._decrypt_data(key, enc)
        self.assertEqual(dec, raw)

    def test_wrong_key_fails(self):
        key = "CorrectKey123"
        wrong_key = "WrongKey456"
        enc = sbm._encrypt_data(key, b"Secret")
        with self.assertRaises(ValueError) as ctx:
            sbm._decrypt_data(wrong_key, enc)
        self.assertIn("Неверный мастер-ключ", str(ctx.exception))


class TestOmniAionEndpoints(unittest.TestCase):
    def test_omniroute_endpoint_format(self):
        res = vs.test_omniroute_endpoint()
        self.assertIn("online", res)
        self.assertIn("latency_ms", res)

    def test_aionui_endpoint_format(self):
        res = vs.test_aionui_endpoint()
        self.assertIn("online", res)
        self.assertIn("latency_ms", res)


if __name__ == "__main__":
    unittest.main()
