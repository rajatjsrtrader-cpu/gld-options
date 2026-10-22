        except Exception:
            prev = {}

    errors, snap = [], None
    if a.fixture:
        snap = parse_cboe(json.load(open(a.fixture)))
    else:
        for name, fn in (("cboe", fetch_cboe), ("yahoo", fetch_yahoo)):
            try:
                snap = fn()
                if snap["contracts"]:
                    break
                errors.append(f"{name}: empty chain")
                snap = None
            except Exception as e:                       # noqa: BLE001 - try the next source, report all at the end
                errors.append(f"{name}: {e!r}")
    if not snap:
        sys.exit("all sources failed: " + " | ".join(errors))
    if errors:
        print("note, earlier source failed:", " | ".join(errors))

    out = build(snap, prev, now)
    with open(a.out, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    n = sum(len(e["s"]) for e in out["expiries"])
    print(f"ok: source={out['source']} spot={out['spot']} expiries={len(out['expiries'])} strike-rows={n} "
          f"snapshots={len(out['hist'])} bytes={len(json.dumps(out, separators=(',', ':')))}")


if __name__ == "__main__":
    main()
