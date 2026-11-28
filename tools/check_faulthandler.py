"""检查三种启动方式下 faulthandler 与 tests 包导入的时机。"""

import faulthandler
import sys
import unittest

print(f"  before: enabled={faulthandler.is_enabled()} "
      f"tests_imported={'tests' in sys.modules}")
suite = unittest.TestLoader().discover("tests")
print(f"  after : enabled={faulthandler.is_enabled()} "
      f"tests_imported={'tests' in sys.modules} "
      f"count={suite.countTestCases()}")
