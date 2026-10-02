import unittest

from textkit import slugify


class SlugifyTest(unittest.TestCase):
    def test_words_joined_by_hyphens(self) -> None:
        self.assertEqual(slugify('Hello, World!'), 'hello-world')

    def test_accents_dropped(self) -> None:
        self.assertEqual(slugify('Crème brûlée'), 'creme-brulee')

    def test_empty(self) -> None:
        self.assertEqual(slugify('  !!  '), '')


if __name__ == '__main__':
    unittest.main()
