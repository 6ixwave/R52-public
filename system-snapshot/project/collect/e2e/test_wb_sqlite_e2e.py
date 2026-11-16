"""E2E тест: Wildberries → SQLite.
Проверяет полный цикл: API → parse → Eye JSON → stage → accept → SQLite.
Запуск: C:/Python315/python.exe test_wb_sqlite_e2e.py
"""
import json
import os
import sys
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from r52.collectors.wb import WBCollector
from r52.intake.eye import Inbox
from r52.domain.normalize import normalize_eye
from r52.service import R52Service
from r52.db import Database

WB_TEST_IDS = ["199298871", "110835207"]

def main():
    tmp = tempfile.mkdtemp(prefix="r52-e2e-wb-")
    print(f"TMP_HOME: {tmp}")
    try:
        # 1. Запуск WBCollector через API
        print("\n--- Step 1: WBCollector API fetch ---")
        collector = WBCollector(data_dir=Path(tmp), use_proxies=False)
        observations = []
        for nm in WB_TEST_IDS:
            url = collector.build_url(nm)
            obs = collector.collect_product(nm, url, title_hint="")
            if obs:
                observations.append(obs)
                o = obs["observation"]
                print(f"  WB {nm}: title={o.get('fields',{}).get('title',{}).get('value','?')[:50]}")
                print(f"    prices={len(o.get('price_candidates',[]))}, "
                      f"media={len(o.get('media',{}).get('items',[]))}, "
                      f"avail={o.get('availability')}")
            else:
                print(f"  WB {nm}: FAILED (no observation)")

        if not observations:
            print("ERROR: No observations collected")
            return 1

        # 2. Запись Eye JSON
        print(f"\n--- Step 2: Write Eye JSON ({len(observations)} files) ---")
        intake_dir = Path(tmp) / "intake"
        intake_dir.mkdir(parents=True, exist_ok=True)
        json_files = []
        for i, obs in enumerate(observations):
            fname = f"jarvis-eye-wb-{obs['observation']['id']}.json"
            fpath = intake_dir / fname
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(obs, f, ensure_ascii=False, indent=2)
            json_files.append(fpath)
            print(f"  {fname} ({fpath.stat().st_size} bytes)")

        # 3. Запуск R52Service
        print("\n--- Step 3: R52Service + SQLite ---")
        db_path = Path(tmp) / "data" / "r52.sqlite3"
        db_path.parent.mkdir(parents=True, exist_ok=True)

        from r52.config import Config
        config = Config(
            home=Path(tmp),
            db_path=db_path,
            intake_folders=(intake_dir,),
        )
        service = R52Service(config)
        service.open()
        print("  App opened OK")

        # 4. Stage + Accept
        print("\n--- Step 4: Stage + Accept ---")
        accepted = 0
        for jf in json_files:
            raw = jf.read_bytes()
            staged = service.stage_bytes(raw, jf.name)
            if staged is None:
                print(f"  {jf.name}: STAGE FAIL")
                continue
            print(f"  {jf.name}: PENDING id={staged}")
            result = service.accept(staged)
            if result:
                accepted += 1
                print(f"    product_id={result.get('product_id')}, "
                      f"observation={result.get('observation_created')}")
            else:
                print(f"    ACCEPT FAIL")

        # 5. SQLite verification
        print("\n" + "=" * 60)
        print("SQLITE VERIFICATION (WB)")
        print("=" * 60)
        conn = db_path.read_bytes()  # just check exists
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("SELECT * FROM product WHERE source='WB'")
        products = cur.fetchall()
        print(f"\nProducts (WB): {len(products)}")
        for p in products:
            d = dict(p)
            print(f"  ID={d['id']} listing={d.get('listing_id','?')}")
            print(f"  Title: {d.get('title','?')[:60]}")
            print(f"  Brand: {d.get('brand','?')}")
            print(f"  URL: {d.get('url','?')[:70]}")
            print(f"  Availability: {d.get('availability','?')}")

            # Prices
            cur2 = conn.cursor()
            cur2.execute("SELECT * FROM price_point WHERE product_id=?", (d["id"],))
            pps = cur2.fetchall()
            for pp in pps:
                ppd = dict(pp)
                amt = ppd.get("amount_minor", 0)
                print(f"  Price: role={ppd.get('role')} amount={amt} ({amt//100} RUB) label={ppd.get('label','?')}")

            # Media
            cur3 = conn.cursor()
            cur3.execute("SELECT * FROM product_media WHERE product_id=?", (d["id"],))
            medias = cur3.fetchall()
            print(f"  Media: {len(medias)} items")
            for med in medias[:3]:
                md = dict(med)
                print(f"    type={md.get('media_type')} url={str(md.get('source_url','?'))[:60]}")

        # Observations
        cur.execute("SELECT * FROM observation WHERE product_id IN (SELECT id FROM product WHERE source='WB')")
        obs_rows = cur.fetchall()
        print(f"\nObservations: {len(obs_rows)}")
        for ob in obs_rows:
            od = dict(ob)
            print(f"  obs_id={od['id']} product={od.get('product_id')} "
                  f"at={od.get('observed_at','?')[:19]} "
                  f"evidence={od.get('evidence_level','?')} "
                  f"norm={od.get('normalized_size','?')}B")

        conn.close()

        # Summary
        print("\n" + "=" * 60)
        if accepted == len(json_files) and len(products) > 0:
            print(f"SUMMARY: PASS — {len(products)} product(s), "
                  f"{sum(1 for _ in obs_rows)} observation(s)")
            result_code = 0
        else:
            print(f"SUMMARY: FAIL — accepted={accepted}/{len(json_files)}, products={len(products)}")
            result_code = 1

        service.close()
        print(f"\nApp stopped. TMP_HOME={tmp}")
        return result_code

    finally:
        # Cleanup
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
