"""Source-only image gate: approved metadata/dependencies + native OCR, no SQL.

Does not claim model inference ran. Weights are verified at first runtime use.
No build credential mechanism is assumed or required.
"""
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    from deploy.validate_linux_runtime import deny_network
    sys.addaudithook(deny_network)
    import inference_releases as releases
    try:
        if not releases.inference_enabled():
            from deploy.runtime_diagnostics import native_probe
            releases.require(native_probe()['code']=='READY','TESSERACT_ENGLISH_REQUIRED')
            print(json.dumps(dict(inference_enabled=False,approved_metadata=False,
                model_inference_run=False,native_ocr_inference_run=False,
                tesseract_available=True,database_contacted=False,production_website_verified=False)))
            return 0
        identifier=releases.configured_identifier(deployment=True)
        releases.require(bool(identifier),'RELEASE_SELECTION_REQUIRED')
        evidence=releases.metadata(identifier,deployment=True,
            profiles=(os.environ.get('PSMS_NLP_RUNTIME','legacy'),'semantic_ocr'))
        from runtime_model_cache import delivery_settings
        delivery_settings(identifier,evidence)
        from deploy.runtime_diagnostics import native_probe
        releases.require(native_probe()['code']=='READY','TESSERACT_ENGLISH_REQUIRED')
        from PIL import Image,ImageDraw,ImageFont
        import pytesseract
        image=Image.new('RGB',(900,150),'white')
        ImageDraw.Draw(image).text((30,35),'SYNTHETIC 42',
            font=ImageFont.truetype('DejaVuSans.ttf',48),fill='black')
        text=pytesseract.image_to_string(image,lang='eng',config='--psm 6',timeout=20)
        releases.require('SYNTHETIC' in text and '42' in text,'TESSERACT_NATIVE_INFERENCE_FAILED')
        print(json.dumps(dict(native_ocr=True,approved_metadata=True,model_inference_run=False,
                              private_weights_in_source=False,database_contacted=False,
                              production_website_verified=False),sort_keys=True))
        return 0
    except releases.ReleaseError as error:
        print(str(error)); return 1
    except Exception:
        print('ONLINE_IMAGE_INFERENCE_GATE_FAILED'); return 1


if __name__=='__main__':
    raise SystemExit(main())
