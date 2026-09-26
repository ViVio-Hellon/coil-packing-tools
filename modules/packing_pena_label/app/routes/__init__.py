# -*- coding: utf-8 -*-
"""画面URLとAPIの受付。

routes は「入力の検証」と「services への受け渡し」だけを行い、
業務ロジックは services に置く（基盤仕様書 4.3）。
"""

from .pages import PageRoutes          # noqa: F401
from .api import ApiRoutes             # noqa: F401
