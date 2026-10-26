"""Aprendizado: intenção explícita e autoria com capacidade limitada."""

import unittest


class LearnPromptTests(unittest.TestCase):
    def test_exact_command_and_empty_request(self):
        from kairos_skills.learn import parse_learn_request

        self.assertEqual(parse_learn_request("/learn  revisar commits "), "revisar commits")
        self.assertIsNone(parse_learn_request("/learning algo"))
        self.assertIsNone(parse_learn_request("texto /learn algo"))
        with self.assertRaises(ValueError):
            parse_learn_request("/learn  ")

    def test_prompt_is_deterministic_and_keeps_user_request(self):
        from kairos_skills.learn import build_learn_prompt

        request = "Transforme a revisão anterior em procedimento."
        prompt = build_learn_prompt(request)
        self.assertEqual(prompt, build_learn_prompt(request))
        self.assertIn(request, prompt)
        self.assertIn("skill_create", prompt)
        self.assertIn("/learn", prompt)
        self.assertIn("esclarecimento", prompt)
