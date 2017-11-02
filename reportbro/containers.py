from typing import List
from .elements import DocElementBase, PageBreakElement, TableElement
from .enums import BandType


class Container(object):
    def __init__(self, container_id, containers, report):
        self.id = container_id
        self.report = report
        self.doc_elements = []  # type: List[DocElementBase]
        self.width = 0
        self.height = 0
        containers[self.id] = self

        self.allow_page_break = True
        self.container_offset_y = 0
        self.sorted_elements = None  # type: List[DocElementBase]
        self.render_elements = None  # type: List[DocElementBase]
        self.explicit_page_break = True
        self.page_y = 0

    def add(self, doc_element):
        self.doc_elements.append(doc_element)

    def is_visible(self):
        return True

    def prepare(self, ctx, pdf_doc=None, only_verify=False):
        self.sorted_elements = []
        for elem in self.doc_elements:
            if pdf_doc or not elem.spreadsheet_hide or only_verify:
                elem.prepare(ctx, pdf_doc=pdf_doc, only_verify=only_verify)
                if not self.allow_page_break:
                    # make sure element can be rendered multiple times (for header/footer)
                    elem.first_render_element = True
                    elem.rendering_complete = False
                self.sorted_elements.append(elem)

        if pdf_doc:
            self.sorted_elements = sorted(self.sorted_elements, key=lambda item: (item.y, item.sort_order))
            # predecessors are only needed for rendering pdf document
            for i, elem in enumerate(self.sorted_elements):
                predecessor = None
                for j in range(i-1, -1, -1):
                    elem2 = self.sorted_elements[j]
                    if elem2.bottom <= elem.y and\
                            (predecessor is None or elem2.bottom > predecessor.bottom):
                        predecessor = elem2
                if predecessor and not isinstance(predecessor, PageBreakElement):
                    elem.set_predecessor(predecessor)
            self.render_elements = []
        else:
            self.sorted_elements = sorted(self.sorted_elements, key=lambda item: (item.y, item.x))

    def get_render_elements_bottom(self):
        if self.render_elements:
            return self.render_elements[-1].render_bottom
        return 0

    def create_render_elements(self, container_height, ctx, pdf_doc):
        i = 0
        new_page = False
        processed_elements = []
        completed_elements = dict()

        set_explicit_page_break = False
        while not new_page and i < len(self.sorted_elements):
            elem = self.sorted_elements[i]
            if elem.predecessor and (elem.predecessor.id not in completed_elements or
                    not elem.predecessor.rendering_complete):
                # predecessor is not completed yet -> start new page
                new_page = True
            else:
                elem_deleted = False
                if isinstance(elem, PageBreakElement):
                    if self.allow_page_break:
                        del self.sorted_elements[i]
                        elem_deleted = True
                        new_page = True
                        set_explicit_page_break = True
                        self.page_y = elem.y
                    else:
                        self.sorted_elements = []
                        return True
                else:
                    complete = False
                    if elem.predecessor:
                        # element is on same page as predecessor element so offset is relative to predecessor
                        offset_y = elem.predecessor.render_bottom + (elem.y - elem.predecessor.bottom)
                    else:
                        if self.allow_page_break:
                            if elem.first_render_element and self.explicit_page_break:
                                offset_y = elem.y - self.page_y
                            else:
                                offset_y = 0
                        else:
                            offset_y = elem.y

                    if elem.is_printed(ctx):
                        if offset_y >= container_height:
                            new_page = True
                        if not new_page:
                            render_elem, complete = elem.get_next_render_element(
                                offset_y, container_height=container_height, ctx=ctx, pdf_doc=pdf_doc)
                            if render_elem:
                                if complete:
                                    processed_elements.append(elem)
                                self.render_elements.append(render_elem)
                    else:
                        processed_elements.append(elem)
                        elem.finish_empty_element(offset_y)
                        complete = True
                    if complete:
                        completed_elements[elem.id] = True
                        del self.sorted_elements[i]
                        elem_deleted = True
                if not elem_deleted:
                    i += 1

        # in case of manual page break the element on the next page is positioned relative
        # to page break position
        self.explicit_page_break = set_explicit_page_break if self.allow_page_break else True

        if len(self.sorted_elements) > 0:
            self.render_elements.append(PageBreakElement(self.report, dict(y=-1)))
            for processed_element in processed_elements:
                # remove dependency to predecessor because successor element is either already added
                # to render_elements or on new page
                for successor in processed_element.successors:
                    successor.predecessor = None
        return len(self.sorted_elements) == 0

    def render_pdf(self, container_offset_x, container_offset_y, pdf_doc, cleanup=False):
        counter = 0
        for render_elem in self.render_elements:
            counter += 1
            if isinstance(render_elem, PageBreakElement):
                break
            render_elem.render_pdf(container_offset_x, container_offset_y, pdf_doc)
            if cleanup:
                render_elem.cleanup()
        self.render_elements = self.render_elements[counter:]

    def render_spreadsheet(self, row, col, ctx, workbook, worksheet):
        max_col = col
        i = 0
        count = len(self.sorted_elements)
        while i < count:
            elem = self.sorted_elements[i]
            j = i + 1
            row_elements = [elem]
            while j < count:
                elem2 = self.sorted_elements[j]
                if elem2.y == elem.y:
                    row_elements.append(elem2)
                else:
                    break
                j += 1
            i = j
            current_row = row
            current_col = col
            for row_element in row_elements:
                tmp_row, current_col = row_element.render_spreadsheet(current_row, current_col, ctx, workbook, worksheet)
                row = max(row, tmp_row)
                if current_col > max_col:
                    max_col = current_col
        return row, max_col

    def is_finished(self):
        return len(self.render_elements) == 0

    def cleanup(self):
        for elem in self.doc_elements:
            elem.cleanup()


class Frame(Container):
    def __init__(self, width, height, container_id, containers, report):
        Container.__init__(self, container_id, containers, report)
        self.width = width
        self.height = height


class ReportBand(Container):
    def __init__(self, band, container_id, containers, report):
        Container.__init__(self, container_id, containers, report)
        self.band = band
        self.width = report.document_properties.page_width -\
                report.document_properties.margin_left - report.document_properties.margin_right
        if band == BandType.content:
            self.height = report.document_properties.content_height
        elif band == BandType.header:
            self.allow_page_break = False
            self.height = report.document_properties.header_size
        elif band == BandType.footer:
            self.allow_page_break = False
            self.height = report.document_properties.footer_size

    def is_visible(self):
        if self.band == BandType.header:
            return self.report.document_properties.header
        elif self.band == BandType.footer:
            return self.report.document_properties.footer
        return True
