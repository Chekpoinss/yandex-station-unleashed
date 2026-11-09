import unittest
from voice_catalog import parse_intent,select,CatalogError


class VoiceCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tracks=[{'id':'a','title':'Song','artist':'First, Second','source':'local','voice_title':'моя песня'},
                     {'id':'b','title':'Song','artist':'Other','source':'soundcloud','voice_artist':'другой'},
                     {'id':'c','title':'Song (Live)','artist':'First','source':'soundcloud'}]

    def test_only_explicit_music_prefixes_are_intents(self):
        for phrase in ('включи свет','не включай трек song','найди рецепт','включи трек','включи трек '+'a'*201):
            self.assertIsNone(parse_intent(phrase))
        self.assertEqual(parse_intent('найди в саундклауде песня')['action'],'search_soundcloud')
        self.assertEqual(parse_intent('поищи в саундклауде джаз')['action'],'search_soundcloud')
        self.assertEqual(parse_intent('поставь песню hello')['action'],'play_catalog')
        self.assertEqual(parse_intent('проиграй трек hello')['action'],'play_catalog')

    def test_duplicate_titles_are_not_randomly_selected(self):
        with self.assertRaises(CatalogError):select({'action':'play_catalog','query':'song'},self.tracks)

    def test_artist_qualification_and_explicit_title_alias(self):
        self.assertEqual(select({'action':'play_catalog','query':'song исполнителя second'},self.tracks)[0]['id'],'a')
        self.assertEqual(select({'action':'play_catalog','query':'моя песня'},self.tracks)[0]['id'],'a')

    def test_unique_partial_title_can_be_played_but_ambiguous_one_is_rejected(self):
        self.assertEqual(select({'action':'play_catalog','query':'song liv'},self.tracks)[0]['id'],'c')
        with self.assertRaises(CatalogError):select({'action':'play_catalog','query':'song'},self.tracks)

    def test_single_speech_recognition_typo_in_english_title_is_corrected(self):
        tracks=[{'id':'impostor','title':'Impostor Syndrome','artist':'Sidney Gish'}]
        self.assertEqual(select({'action':'play_catalog','query':'imposter syndrome'},tracks)[0]['id'],'impostor')

    def test_artist_queue_includes_both_sources_and_collaborations(self):
        self.assertEqual([t['id'] for t in select({'action':'play_artist','query':'first'},self.tracks)],['a','c'])
        self.assertEqual(select({'action':'play_artist','query':'другой'},self.tracks)[0]['id'],'b')

    def test_missing_track_or_artist_does_not_fall_back_to_unrelated_version(self):
        for action,query in (('play_catalog','song sped up'),('play_artist','unknown')):
            with self.assertRaises(CatalogError):select({'action':action,'query':query},self.tracks)

    def test_search_returns_matches_without_selecting_audio(self):
        self.assertEqual(len(select({'action':'search_catalog','query':'song'},self.tracks)),3)
        self.assertEqual(select({'action':'search_catalog','query':'missing'},self.tracks),[])
