"""Stand-in for cargo in dry runs: prints output in cargo's test summary format."""

import sys

if sys.argv[1:2] == ["test"]:
    print("running 3 tests\ntest a ... ok\ntest b ... ok\ntest c ... ok\n")
    print("test result: ok. 3 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out")
else:
    print("    Finished `dev` profile [unoptimized + debuginfo] target(s)")
