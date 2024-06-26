import numpy as np
import torch
from transformers import T5Tokenizer, T5ForConditionalGeneration
from .base import LLMTransformer
from tqdm import tqdm
from more_itertools import chunked

def create_pairs(num : int):
    array = []
    for i in range(num):
        for j in range(num):
            array.append((i, j))
    return array

# Based on https://github.com/ielab/llm-rankers/blob/main/llmrankers/pairwise.py

class PairT5(LLMTransformer):
    template = """Given a query "{query}", which of the following two passages is more relevant to the query?

Passage A: "{doc1}"

Passage B: "{doc2}"

Output Passage A or Passage B:"""

    def __init__(self, 
                 model_path : str = 'google/flan-t5-xl', 
                 batch_size : int = 16, 
                 bfloat16 : bool = None, 
                 **kwargs):
        super().__init__(**kwargs)
        self.batch_size = batch_size
        self.tokenizer = T5Tokenizer.from_pretrained(model_path, return_dict=False, legacy=False, use_fast=True)
        self.model = T5ForConditionalGeneration.from_pretrained(model_path).cuda().eval()
        if bfloat16 is None:
            try:
                self.model = self.model.bfloat16()
                bfloat16 = True
            except:
                bfloat16 = False
        elif bfloat16:
            self.model = self.model.bfloat16()
        self.bfloat16 = bfloat16
        self.decoder_input_ids = self.tokenizer.encode("<pad> Passage",
                                                           return_tensors="pt",
                                                           add_special_tokens=False).to(self.model.device)
        self.decoder_input_ids = self.decoder_input_ids.repeat(self.batch_size, 1)
        self.A, self.B = self.tokenizer.encode("A")[0], self.tokenizer.encode("B")[0]

    def score(self, query : str, doc_texts : list, **kwargs):
        idx = create_pairs(len(doc_texts))
        score_matrix = np.zeros((len(doc_texts), len(doc_texts)))
        progress = not self.verbose
        for batch in tqdm(chunked(idx, self.batch_size), unit='batch', disable=progress):
            prompts = [self.template.format(query=query, doc1=doc_texts[i], doc2=doc_texts[j]) for i, j in batch]
            inputs = self.tokenizer(prompts,
                                       padding='longest',
                                       return_tensors="pt").input_ids.to(self.model.device)
            with torch.no_grad():
                outputs = self.model.generate(inputs, decoder_input_ids=self.decoder_input_ids, max_new_tokens=2)
            outputs = self.tokenizer.batch_decode(outputs, skip_special_tokens=True)

            for k in range(0, len(outputs)):
                i, j = batch[k]
                output1 = outputs[i]
                if output1 == "Passage A":
                    score_matrix[i, j] = 1
                    score_matrix[j, i] = 0
                elif output1 == "Passage B":
                    score_matrix[i, j] = 0
                    score_matrix[j, i] = 1
                else:  # conflict
                    score_matrix[i, j] = 0.5
                    score_matrix[j, i] = 0.5
        
        for i in range(len(doc_texts)):
            for j in range(len(doc_texts)):
                if score_matrix[i, j] > 0.5 and score_matrix[j, i] < 0.5: score_matrix[i, j], score_matrix[j, i] = 1., 0.
                elif score_matrix[i, j] < 0.5 and score_matrix[j, i] > 0.5: score_matrix[i, j], score_matrix[j, i] = 0., 1.
                else: score_matrix[i, j], score_matrix[j, i] = 0.5, 0.5
        
        scores = score_matrix.sum(axis=1)
        return np.argsort(scores)[::-1]