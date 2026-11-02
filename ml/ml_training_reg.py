"""
Compatibility wrapper for ml/train_regression.py
"""
import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.train_regression import main

if __name__ == "__main__":
    main()
