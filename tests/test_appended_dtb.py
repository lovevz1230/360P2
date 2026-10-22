"""Linux 6.12 includes drivers/of/empty_root.dtb before the board DTB."""
from pathlib import Path
import sys
import unittest

from test_collect_artifacts import image
from test_verify_image import encode, fixture, node

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from verify_image import inspect


class AppendedDeviceTree(unittest.TestCase):
    def test_builtin_empty_tree_does_not_hide_appended_board(self):
        kernel = b'kernel-code'+encode(node(''))+bytes(32)+encode(fixture())
        errors,kind = inspect(image(kernel),'initramfs')
        self.assertEqual((errors,kind),([],'uimage'))

    def test_interior_p2_tree_cannot_override_wrong_appended_board(self):
        wrong = fixture(); wrong[1]['compatible'] = b'asus,rt-ac1200\0'
        kernel = encode(fixture())+bytes(32)+encode(wrong)
        errors,kind = inspect(image(kernel),'initramfs')
        self.assertIn('board_identity',errors)

    def test_appended_empty_tree_cannot_use_interior_p2_identity(self):
        kernel = encode(fixture())+bytes(32)+encode(node(''))
        errors,kind = inspect(image(kernel),'initramfs')
        self.assertIn('board_identity',errors)

    def test_p2_tree_with_unexplained_kernel_tail_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'appended_dtb'):
            inspect(image(encode(fixture())+b'unexplained-tail'),'initramfs')


if __name__ == '__main__': unittest.main()
