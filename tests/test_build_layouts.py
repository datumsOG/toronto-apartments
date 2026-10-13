import unittest
from build.layouts import join


class Layouts(unittest.TestCase):
    def test_page_joins_to_the_one_building_that_links_to_it(self):
        out, none, several = join({'https://l.example/a/': 12}, [(7, 'https://l.example/a?unit=3')])
        self.assertEqual(out, {7: {'url': 'https://l.example/a/', 'plans': 12}})
        self.assertEqual((none, several), (0, 0))

    def test_a_unit_page_under_the_building_page_counts(self):
        out, _, _ = join({'https://l.example/a': 2}, [(7, 'https://l.example/a/suite-1')])
        self.assertIn(7, out)

    def test_no_building_and_several_buildings_are_left_out_and_counted(self):
        out, none, several = join({'https://l.example/a': 1, 'https://l.example/b': 1},
                                  [(1, 'https://l.example/b'), (2, 'https://l.example/b')])
        self.assertEqual((out, none, several), ({}, 1, 1))

    def test_a_data_address_is_never_linked(self):
        out, _, _ = join({'https://l.example/api/v1/x': 9}, [(1, 'https://l.example/api/v1/x')])
        self.assertEqual(out, {})


if __name__ == '__main__':
    unittest.main()
