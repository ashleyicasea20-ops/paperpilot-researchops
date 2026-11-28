"""Start approved PostgreSQL tests in a new isolated process."""
if __name__=="__main__":
    from tests.postgres.launcher import main
    raise SystemExit(main())
